"""严格验证「磁盘 Session 恢复」的独立探针（不属于 30 例基线）。

用途：证明 Session Recovery 是真实经过
    create -> 写入磁盘 -> 销毁 Agent/Conversation -> 新 Agent -> SessionManager.resume

它和 30 例基线里的 SESSION_* 用例互不影响：
基线用例只验证多轮对话上下文，不验证跨进程/跨对象的磁盘恢复。

用法：
    python benchmark/session_resume_probe.py
输出：
    benchmark/results/session_probe/probe_result.json
"""

from __future__ import annotations

import asyncio
import gc
import json
import shutil
import time
from pathlib import Path

from wendream.agent import (
    Agent,
    ErrorEvent,
    PermissionRequest,
    PermissionResponse,
    StreamText,
)
from wendream.client import create_client
from wendream.config import load_config
from wendream.conversation import ConversationManager, Message
from wendream.memory.instructions import load_instructions
from wendream.memory.session import SESSIONS_DIR, SessionManager
from wendream.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    RuleEngine,
)
from wendream.permissions.modes import PermissionMode
from wendream.tools import create_default_registry

TURN_TIMEOUT = 180.0
BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "results" / "session_probe"


def build_agent(work_dir: Path, provider) -> Agent:
    """用真实的 client / registry / permission checker 构造 Agent。"""
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(work_dir)),
        rule_engine=RuleEngine(
            user_rules_path=Path.home() / ".wendream" / "permissions.yaml",
            project_rules_path=work_dir / ".wendream" / "permissions.yaml",
            local_rules_path=work_dir / ".wendream" / "permissions.local.yaml",
        ),
        mode=PermissionMode.DEFAULT,
    )
    return Agent(
        client=create_client(provider),
        registry=create_default_registry(),
        protocol=provider.protocol,
        work_dir=str(work_dir),
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=load_instructions(str(work_dir)),
    )


async def run_turn(agent: Agent, conversation: ConversationManager, prompt: str) -> str:
    conversation.add_user_message(prompt)
    chunks: list[str] = []

    async def drive() -> None:
        async for event in agent.run(conversation):
            if isinstance(event, StreamText):
                chunks.append(event.text)
            elif isinstance(event, PermissionRequest):
                # 非交互：必须先给出确定性答案，否则 agent 会永久 await
                if not event.future.done():
                    event.future.set_result(PermissionResponse.ALLOW)
            elif isinstance(event, ErrorEvent):
                chunks.append(f"\n[error] {event.message}\n")

    await asyncio.wait_for(drive(), timeout=TURN_TIMEOUT)
    return "".join(chunks).strip()


def reset_dir(path: Path) -> None:
    if path.exists():
        for _ in range(5):
            try:
                shutil.rmtree(path)
                break
            except (PermissionError, OSError):
                time.sleep(0.3)
    path.mkdir(parents=True, exist_ok=True)


def close_session(session) -> None:
    """关闭 Session 内部持有的 jsonl 文件句柄（字段名是 _file）。"""
    handle = getattr(session, "_file", None)
    if handle is not None and not getattr(handle, "closed", True):
        handle.close()


def message_texts(messages: list[Message]) -> list[str]:
    out = []
    for m in messages:
        if isinstance(m.content, str):
            out.append(m.content)
        else:
            out.append(json.dumps(m.content, ensure_ascii=False))
    return out


async def probe_single_secret(work_root: Path, provider) -> dict:
    """S1：保存一个秘密 -> 销毁 -> 新 Agent -> resume -> 追问。"""
    work = work_root / "session_001"
    reset_dir(work)

    manager = SessionManager(str(work))
    session_a = manager.create()
    session_id = session_a.session_id
    session_path = str((work / SESSIONS_DIR / f"{session_id}.jsonl").resolve())

    agent_a = build_agent(work, provider)
    conversation_a = ConversationManager()
    seed_prompt = "Session A 秘密编号是 ALPHA-7788。请只回复 OK。"
    reply_a = await run_turn(agent_a, conversation_a, seed_prompt)

    # 真实落盘：把本轮 user/assistant 追加进 session jsonl
    session_a.append(Message(role="user", content=seed_prompt))
    session_a.append(Message(role="assistant", content=reply_a))
    close_session(session_a)

    messages_before_destroy = len(conversation_a.history)
    agent_a_id = id(agent_a)
    conversation_a_id = id(conversation_a)

    # 彻底销毁 Agent A 与其 ConversationManager
    del agent_a, conversation_a, session_a
    gc.collect()

    # 全新 Agent B + 从磁盘恢复
    agent_b = build_agent(work, provider)
    conversation_b = ConversationManager()
    resume_called = False
    resumed = manager.resume(session_id)
    if resumed is not None:
        resume_called = True
        conversation_b.history = list(resumed.messages)

    messages_after_resume = len(conversation_b.history)

    final_response = await run_turn(agent_b, conversation_b, "秘密编号是什么？")
    agent_b_id = id(agent_b)

    return {
        "case": "SESSION_001_disk_resume",
        "agent_a_object_id": agent_a_id,
        "agent_b_object_id": agent_b_id,
        "conversation_a_object_id": conversation_a_id,
        "conversation_b_object_id": id(conversation_b),
        "agent_ids_differ": agent_a_id != agent_b_id,
        "session_id": session_id,
        "session_path": session_path,
        "session_file_size": Path(session_path).stat().st_size if Path(session_path).exists() else 0,
        "resume_called": resume_called,
        "messages_before_destroy": messages_before_destroy,
        "messages_after_resume": messages_after_resume,
        "resumed_message_texts": message_texts(list(resumed.messages)) if resumed else [],
        "final_response": final_response,
        "secret_recovered": "ALPHA-7788" in final_response,
        "seed_reply": reply_a,
    }


async def probe_isolation(work_root: Path, provider) -> dict:
    """S2：两个独立磁盘 Session，验证恢复后互不串味。"""
    work = work_root / "session_002"
    reset_dir(work)

    manager = SessionManager(str(work))

    # --- Session A: COLOR_A = RED ---
    session_a = manager.create()
    session_a_id = session_a.session_id
    session_a_path = str((work / SESSIONS_DIR / f"{session_a_id}.jsonl").resolve())
    agent_a = build_agent(work, provider)
    conv_a = ConversationManager()
    seed_a = "COLOR_A = RED。请只回复 OK。"
    reply_a = await run_turn(agent_a, conv_a, seed_a)
    session_a.append(Message(role="user", content=seed_a))
    session_a.append(Message(role="assistant", content=reply_a))
    close_session(session_a)
    del agent_a, conv_a, session_a
    gc.collect()

    # --- Session B: COLOR_B = BLUE ---
    session_b = manager.create()
    session_b_id = session_b.session_id
    session_b_path = str((work / SESSIONS_DIR / f"{session_b_id}.jsonl").resolve())
    agent_b = build_agent(work, provider)
    conv_b = ConversationManager()
    seed_b = "COLOR_B = BLUE。请只回复 OK。"
    reply_b = await run_turn(agent_b, conv_b, seed_b)
    session_b.append(Message(role="user", content=seed_b))
    session_b.append(Message(role="assistant", content=reply_b))
    close_session(session_b)
    del agent_b, conv_b, session_b
    gc.collect()

    # --- 恢复 A ---
    agent_a2 = build_agent(work, provider)
    conv_a2 = ConversationManager()
    resumed_a = manager.resume(session_a_id)
    if resumed_a is not None:
        conv_a2.history = list(resumed_a.messages)
    answer_a = await run_turn(agent_a2, conv_a2, "COLOR_A 是什么颜色？")

    # --- 恢复 B ---
    agent_b2 = build_agent(work, provider)
    conv_b2 = ConversationManager()
    resumed_b = manager.resume(session_b_id)
    if resumed_b is not None:
        conv_b2.history = list(resumed_b.messages)
    answer_b = await run_turn(agent_b2, conv_b2, "COLOR_B 是什么颜色？")

    a_texts = " ".join(message_texts(list(resumed_a.messages))) if resumed_a else ""
    b_texts = " ".join(message_texts(list(resumed_b.messages))) if resumed_b else ""

    return {
        "case": "SESSION_002_two_sessions_isolation",
        "session_a_id": session_a_id,
        "session_a_path": session_a_path,
        "session_b_id": session_b_id,
        "session_b_path": session_b_path,
        "session_ids_differ": session_a_id != session_b_id,
        "resume_a_called": resumed_a is not None,
        "resume_b_called": resumed_b is not None,
        "resumed_a_messages": len(resumed_a.messages) if resumed_a else 0,
        "resumed_b_messages": len(resumed_b.messages) if resumed_b else 0,
        "a_history_contains_BLUE": "BLUE" in a_texts.upper(),
        "b_history_contains_RED": "RED" in b_texts.upper(),
        "answer_a": answer_a,
        "answer_b": answer_b,
        "a_recovered_RED": "RED" in answer_a.upper(),
        "b_recovered_BLUE": "BLUE" in answer_b.upper(),
        "isolation_ok": ("BLUE" not in a_texts.upper()) and ("RED" not in b_texts.upper()),
    }


async def main() -> None:
    config = load_config()
    provider = config.providers[0]
    work_root = BASE / "workspaces" / "_session_probe"
    reset_dir(work_root)

    started = time.perf_counter()
    results = {
        "provider": provider.name,
        "model": provider.model,
        "s1": await probe_single_secret(work_root, provider),
        "s2": await probe_isolation(work_root, provider),
    }
    results["elapsed_seconds"] = round(time.perf_counter() - started, 2)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "probe_result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nwritten -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
