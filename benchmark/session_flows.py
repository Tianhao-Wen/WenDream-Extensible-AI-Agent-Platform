"""Session 恢复 / 隔离的真实流程实现（供正式 Benchmark case 调用）。

这里只使用项目自身的真实组件：
    Agent + create_client + PermissionChecker + SessionManager.create/resume
不使用任何 mock，也不由 Runner 缓存答案再注入。

两条流程：
    run_disk_resume   : create -> 落盘 -> 销毁 Agent/Conversation/Session
                        -> 新 Agent -> SessionManager.resume -> 追问
    run_session_isolation : 两个独立 session，各自恢复，验证互不串味
"""

from __future__ import annotations

import asyncio
import gc
import shutil
import time
from pathlib import Path
from typing import Any

from wendream.agent import (
    Agent,
    ErrorEvent,
    PermissionRequest,
    PermissionResponse,
    StreamText,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
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


class SessionStageTimeout(Exception):
    """某个 session 阶段超过了单轮上限（180s）。"""

    def __init__(self, stage: str, turn_index: int, elapsed: float):
        super().__init__(f"stage '{stage}' exceeded {TURN_TIMEOUT}s (elapsed={elapsed:.2f}s)")
        self.stage = stage
        self.turn_index = turn_index
        self.elapsed = elapsed


class FlowRecorder:
    """Session 流程的阶段与计数观测（只读，不改变执行语义）。"""

    def __init__(self, emit=None):
        self.stages: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self.current_stage = ""
        self.last_completed_stage = ""
        self.stage_failed = ""
        self.llm_call_count = 0
        self.tool_call_count = 0
        self.permission_request_count = 0
        self.permission_denied_count = 0
        self.turn_index = 0
        # --- 观测层（不改变任何执行语义）---
        # 一次工具调用 = 一条记录；ToolUseEvent 创建、ToolResultEvent 回填，
        # 因此“一对事件”不会被算成两次调用。
        self.tool_calls: list[dict[str, Any]] = []
        self._pending_calls: dict[str, int] = {}
        self.permission_decisions: list[dict[str, Any]] = []
        self.transcript: list[dict[str, str]] = []
        self._emit = emit or (lambda line: print(line, flush=True))

    def start(self, name: str) -> None:
        self._current = {"stage": name, "start": time.perf_counter(), "end": None, "latency": None}
        self.current_stage = name
        self._emit(f"      · session_stage={name}")

    def end(self) -> None:
        if self._current is None:
            return
        self._current["end"] = time.perf_counter()
        self._current["latency"] = round(
            self._current["end"] - self._current["start"], 3
        )
        self.stages.append(self._current)
        self.last_completed_stage = self._current["stage"]
        self.current_stage = ""
        self._current = None

    def note_event(self, event) -> None:
        if isinstance(event, UsageEvent):
            self.llm_call_count += 1
        elif isinstance(event, ToolUseEvent):
            self.tool_call_count += 1
            record = {
                "sequence": self.tool_call_count,
                "stage": self.current_stage or self.last_completed_stage,
                "tool_name": event.tool_name,
                "tool_id": event.tool_id,
                "arguments": dict(event.arguments or {}),
            }
            self.tool_calls.append(record)
            self._pending_calls[event.tool_id] = len(self.tool_calls) - 1
        elif isinstance(event, ToolResultEvent):
            index = self._pending_calls.pop(event.tool_id, None)
            if index is None:  # 没有对应 ToolUseEvent 的结果，单独记一条，不丢信息
                record = {
                    "sequence": len(self.tool_calls) + 1,
                    "stage": self.current_stage or self.last_completed_stage,
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "arguments": {},
                }
                self.tool_calls.append(record)
                index = len(self.tool_calls) - 1
            self.tool_calls[index].update(
                {
                    "result": str(event.output)[:4000],
                    "is_error": bool(event.is_error),
                    "elapsed": round(float(event.elapsed), 3),
                }
            )

    def note_permission(self, tool, arguments, decision) -> None:
        if str(getattr(decision, "effect", "")) == "deny":
            self.permission_denied_count += 1
        self.permission_decisions.append(
            {
                "source": "permission_checker",
                "tool_name": getattr(tool, "name", ""),
                "operation": getattr(tool, "category", ""),
                "path": str((arguments or {}).get("file_path") or (arguments or {}).get("path") or ""),
                "decision": str(getattr(decision, "effect", "")),
                "reason": str(getattr(decision, "reason", "")),
                "rule": str(getattr(decision, "rule", "")),
                "timestamp": time.time(),
            }
        )

    def add_transcript(self, role: str, content: str) -> None:
        self.transcript.append({"role": role, "content": content})

    def summary(self) -> dict[str, Any]:
        return {
            "stages": self.stages,
            "last_completed_stage": self.last_completed_stage,
            "timeout_stage": self.stage_failed or self.current_stage,
            "llm_call_count": self.llm_call_count,
            "tool_call_count": self.tool_call_count,
            "permission_request_count": self.permission_request_count,
            "permission_denied_count": self.permission_denied_count,
            "tool_calls": self.tool_calls,
            "permission_decisions": self.permission_decisions,
            "transcript": self.transcript,
        }


def build_agent(work_dir: Path, provider, recorder: FlowRecorder | None = None) -> Agent:
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(work_dir)),
        rule_engine=RuleEngine(
            user_rules_path=Path.home() / ".wendream" / "permissions.yaml",
            project_rules_path=work_dir / ".wendream" / "permissions.yaml",
            local_rules_path=work_dir / ".wendream" / "permissions.local.yaml",
        ),
        mode=PermissionMode.DEFAULT,
        observer=recorder.note_permission if recorder is not None else None,
    )
    return Agent(
        client=create_client(provider),
        registry=create_default_registry(
            work_dir=str(work_dir), restrict_bash_to_workspace=True
        ),
        protocol=provider.protocol,
        work_dir=str(work_dir),
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=load_instructions(str(work_dir)),
    )


async def _turn(
    agent: Agent,
    conversation: ConversationManager,
    prompt: str,
    recorder: FlowRecorder,
    stage_name: str,
) -> str:
    recorder.start(stage_name)
    recorder.turn_index += 1
    start = time.perf_counter()
    conversation.add_user_message(prompt)
    recorder.add_transcript("user", prompt)
    chunks: list[str] = []

    async def drive() -> None:
        async for event in agent.run(conversation):
            recorder.note_event(event)
            if isinstance(event, StreamText):
                chunks.append(event.text)
            elif isinstance(event, PermissionRequest):
                recorder.permission_request_count += 1
                if not event.future.done():
                    event.future.set_result(PermissionResponse.ALLOW)
            elif isinstance(event, ErrorEvent):
                chunks.append(f"[error] {event.message}")

    try:
        await asyncio.wait_for(drive(), timeout=TURN_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        elapsed = time.perf_counter() - start
        recorder.stage_failed = stage_name
        raise SessionStageTimeout(stage_name, recorder.turn_index, elapsed) from None
    recorder.end()
    reply = "".join(chunks).strip()
    recorder.add_transcript("assistant", reply)
    return reply


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
    handle = getattr(session, "_file", None)
    if handle is not None and not getattr(handle, "closed", True):
        handle.close()


def summarize_messages(messages: list[Message], limit: int = 200) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        content = m.content if isinstance(m.content, str) else str(m.content)
        out.append({"role": m.role, "content": content[:limit]})
    return out


def _session_path(work: Path, session_id: str) -> Path:
    return (work / SESSIONS_DIR / f"{session_id}.jsonl").resolve()


def _timeout_payload(
    recorder: FlowRecorder, exc: SessionStageTimeout, started: float
) -> dict[str, Any]:
    """超时证据：明确区分 TIMEOUT 与 INFRASTRUCTURE_ERROR。"""
    payload: dict[str, Any] = {
        "status": "TIMEOUT",
        "timeout_stage": exc.stage,
        "timeout_turn_index": exc.turn_index,
        "timeout_elapsed_seconds": round(exc.elapsed, 3),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "error": f"stage '{exc.stage}' exceeded {TURN_TIMEOUT}s",
    }
    payload.update(recorder.summary())
    return payload


def run_disk_resume(
    workspace: Path,
    provider,
    seed_prompt: str,
    followup_prompt: str,
    expect_in_final: str,
    emit=None,
) -> dict[str, Any]:
    work = Path(workspace)
    recorder = FlowRecorder(emit=emit)
    started = time.perf_counter()
    reset_dir(work)

    manager = SessionManager(str(work))
    agent_a_id = None
    conv_a_id = None
    messages_before_destroy = 0
    resume_called = False
    seed_reply = ""

    try:
        recorder.start("create_session_a")
        session_a = manager.create()
        session_id = session_a.session_id
        path_a = _session_path(work, session_id)
        recorder.end()

        agent_a = build_agent(work, provider, recorder)
        conv_a = ConversationManager()
        seed_reply = asyncio.run(_turn(agent_a, conv_a, seed_prompt, recorder, "seed_session_a"))

        recorder.start("persist_session_a")
        session_a.append(Message(role="user", content=seed_prompt))
        session_a.append(Message(role="assistant", content=seed_reply))
        close_session(session_a)
        recorder.end()

        agent_a_id = id(agent_a)
        conv_a_id = id(conv_a)
        messages_before_destroy = len(conv_a.history)

        recorder.start("destroy_agent_a")
        del agent_a, conv_a, session_a
        gc.collect()
        recorder.end()

        agent_b = build_agent(work, provider, recorder)
        conv_b = ConversationManager()

        recorder.start("resume_session_a")
        resumed = manager.resume(session_id)
        resume_called = resumed is not None
        if resumed is not None:
            conv_b.history = list(resumed.messages)
        messages_after_resume = len(conv_b.history)
        resumed_texts = [
            (m.content if isinstance(m.content, str) else str(m.content))
            for m in (resumed.messages if resumed else [])
        ]
        history_contains_seed = any(seed_prompt[:20] in text for text in resumed_texts)
        recorder.end()

        final_response = asyncio.run(
            _turn(agent_b, conv_b, followup_prompt, recorder, "query_session_a")
        )
        status = "OK"
    except SessionStageTimeout as exc:
        payload = _timeout_payload(recorder, exc, started)
        payload.update(
            {
                "agent_a_object_id": agent_a_id,
                "agent_b_object_id": None,
                "agent_ids_differ": False,
                "session_id": locals().get("session_id"),
                "session_path": str(locals().get("path_a", "")),
                "session_file_exists": bool(locals().get("path_a") and Path(path_a).exists()),
                "session_file_size": Path(path_a).stat().st_size if locals().get("path_a") and Path(path_a).exists() else 0,
                "resume_called": resume_called,
                "messages_before_destroy": messages_before_destroy,
                "messages_after_resume": 0,
                "history_contains_seed": False,
                "resumed_message_summary": [],
                "seed_reply": seed_reply,
                "final_prompt": followup_prompt,
                "final_response": "",
                "expect_in_final": expect_in_final,
                "expected_found": False,
                "followup_contains_answer": expect_in_final.lower() in followup_prompt.lower(),
            }
        )
        return payload

    return {
        "status": "OK",
        "agent_a_object_id": agent_a_id,
        "agent_b_object_id": id(agent_b),
        "agent_ids_differ": agent_a_id != id(agent_b),
        "conversation_a_object_id": conv_a_id,
        "conversation_b_object_id": id(conv_b),
        "session_id": session_id,
        "session_path": str(path_a),
        "session_file_exists": path_a.exists(),
        "session_file_size": path_a.stat().st_size if path_a.exists() else 0,
        "resume_called": resume_called,
        "messages_before_destroy": messages_before_destroy,
        "messages_after_resume": messages_after_resume,
        "history_contains_seed": history_contains_seed,
        "resumed_message_summary": summarize_messages(list(resumed.messages)) if resumed else [],
        "seed_reply": seed_reply,
        "final_prompt": followup_prompt,
        "final_response": final_response,
        "expect_in_final": expect_in_final,
        "expected_found": expect_in_final.lower() in final_response.lower(),
        "followup_contains_answer": expect_in_final.lower() in followup_prompt.lower(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        **recorder.summary(),
    }


def detect_canary_leakage(text: str, forbidden: list[str]) -> list[str]:
    """在文本中查找被禁止的 **canary 值**（大小写不敏感的子串）。

    只做“值”匹配。模型在自然语言里提到 `A_SECRET` 这种**键名**时不得判为泄漏：
    提到键名不等于知道了另一个会话的值。
    """
    haystack = (text or "").upper()
    return [token for token in forbidden if token and token.upper() in haystack]


def compute_isolation(
    texts_a: str,
    texts_b: str,
    forbid_in_a: list[str],
    forbid_in_b: list[str],
) -> dict[str, Any]:
    """纯函数：给定两个会话的可见文本与各自的禁入 canary，算出隔离结论。"""
    leakage_a = detect_canary_leakage(texts_a, forbid_in_a)
    leakage_b = detect_canary_leakage(texts_b, forbid_in_b)
    return {
        "leakage_in_a": leakage_a,
        "leakage_in_b": leakage_b,
        "cross_contamination": leakage_a + leakage_b,
        "isolation_ok": not leakage_a and not leakage_b,
    }


def run_session_isolation(
    workspace: Path,
    provider,
    seed_a: str,
    seed_b: str,
    question_a: str,
    question_b: str,
    expect_a: str,
    expect_b: str,
    forbid_in_a: list[str],
    forbid_in_b: list[str],
    emit=None,
) -> dict[str, Any]:
    work = Path(workspace)
    recorder = FlowRecorder(emit=emit)
    started = time.perf_counter()
    reset_dir(work)
    manager = SessionManager(str(work))

    try:
        # --- Session A ---
        recorder.start("create_session_a")
        sa = manager.create()
        sa_id = sa.session_id
        sa_path = _session_path(work, sa_id)
        recorder.end()

        agent_a = build_agent(work, provider, recorder)
        conv_a = ConversationManager()
        reply_a = asyncio.run(_turn(agent_a, conv_a, seed_a, recorder, "seed_session_a"))

        recorder.start("close_session_a")
        sa.append(Message(role="user", content=seed_a))
        sa.append(Message(role="assistant", content=reply_a))
        close_session(sa)
        recorder.end()
        del agent_a, conv_a, sa
        gc.collect()

        # --- Session B ---
        recorder.start("create_session_b")
        sb = manager.create()
        sb_id = sb.session_id
        sb_path = _session_path(work, sb_id)
        recorder.end()

        agent_b = build_agent(work, provider, recorder)
        conv_b = ConversationManager()
        reply_b = asyncio.run(_turn(agent_b, conv_b, seed_b, recorder, "seed_session_b"))

        recorder.start("close_session_b")
        sb.append(Message(role="user", content=seed_b))
        sb.append(Message(role="assistant", content=reply_b))
        close_session(sb)
        recorder.end()
        del agent_b, conv_b, sb
        gc.collect()

        # --- 恢复 A ---
        recorder.start("resume_session_a")
        agent_a2 = build_agent(work, provider, recorder)
        conv_a2 = ConversationManager()
        resumed_a = manager.resume(sa_id)
        if resumed_a is not None:
            conv_a2.history = list(resumed_a.messages)
        recorder.end()
        answer_a = asyncio.run(
            _turn(agent_a2, conv_a2, question_a, recorder, "query_session_a")
        )

        # --- 恢复 B ---
        recorder.start("resume_session_b")
        agent_b2 = build_agent(work, provider, recorder)
        conv_b2 = ConversationManager()
        resumed_b = manager.resume(sb_id)
        if resumed_b is not None:
            conv_b2.history = list(resumed_b.messages)
        recorder.end()
        answer_b = asyncio.run(
            _turn(agent_b2, conv_b2, question_b, recorder, "query_session_b")
        )
        status = "OK"
    except SessionStageTimeout as exc:
        payload = _timeout_payload(recorder, exc, started)
        payload.update(
            {
                "session_a_id": locals().get("sa_id"),
                "session_a_path": str(locals().get("sa_path", "")),
                "session_b_id": locals().get("sb_id"),
                "session_b_path": str(locals().get("sb_path", "")),
                "session_ids_differ": locals().get("sa_id") != locals().get("sb_id"),
                "session_paths_differ": str(locals().get("sa_path")) != str(locals().get("sb_path")),
                "resume_a_called": False,
                "resume_b_called": False,
                "resumed_a_summary": [],
                "resumed_b_summary": [],
                "answer_a": "",
                "answer_b": "",
                "expect_a": expect_a,
                "expect_b": expect_b,
                "a_expected_found": False,
                "b_expected_found": False,
                "leakage_in_a": [],
                "leakage_in_b": [],
                "cross_contamination": [],
                "isolation_ok": False,
            }
        )
        return payload

    texts_a = " ".join(
        (m.content if isinstance(m.content, str) else str(m.content))
        for m in (resumed_a.messages if resumed_a else [])
    ).upper()
    texts_b = " ".join(
        (m.content if isinstance(m.content, str) else str(m.content))
        for m in (resumed_b.messages if resumed_b else [])
    ).upper()

    isolation = compute_isolation(texts_a, texts_b, forbid_in_a, forbid_in_b)

    return {
        "status": "OK",
        "session_a_id": sa_id,
        "session_a_path": str(sa_path),
        "session_b_id": sb_id,
        "session_b_path": str(sb_path),
        "session_ids_differ": sa_id != sb_id,
        "session_paths_differ": str(sa_path) != str(sb_path),
        "resume_a_called": resumed_a is not None,
        "resume_b_called": resumed_b is not None,
        "resumed_a_summary": summarize_messages(list(resumed_a.messages)) if resumed_a else [],
        "resumed_b_summary": summarize_messages(list(resumed_b.messages)) if resumed_b else [],
        "answer_a": answer_a,
        "answer_b": answer_b,
        "expect_a": expect_a,
        "expect_b": expect_b,
        "a_expected_found": expect_a.lower() in answer_a.lower(),
        "b_expected_found": expect_b.lower() in answer_b.lower(),
        "leakage_in_a": isolation["leakage_in_a"],
        "leakage_in_b": isolation["leakage_in_b"],
        "cross_contamination": isolation["cross_contamination"],
        "isolation_ok": isolation["isolation_ok"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        **recorder.summary(),
    }
