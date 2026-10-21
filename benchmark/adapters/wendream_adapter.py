from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from wendream.agent import (
    Agent,
    ErrorEvent,
    PermissionRequest,
    PermissionResponse,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from wendream.client import create_client
from wendream.config import load_config
from wendream.conversation import ConversationManager, Message
from wendream.memory.instructions import load_instructions
from wendream.memory.session import SessionManager
from wendream.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, RuleEngine
from wendream.permissions.modes import PermissionMode
from wendream.tools import create_default_registry


DEFAULT_TURN_TIMEOUT_SECONDS = 180.0
VALID_PERMISSION_POLICIES = {"deny", "allow", "allow_always"}


class WenDreamAdapter:
    """Benchmark adapter that executes the real WenDream runtime."""

    def __init__(
        self,
        work_dir: str | Path | None = None,
        provider_index: int = 0,
        enable_fallback: bool = False,
        permission_policy: str = "deny",
        timeout_seconds: float | None = DEFAULT_TURN_TIMEOUT_SECONDS,
        on_progress: Any | None = None,
    ):
        self.work_dir = str(Path(work_dir) if work_dir is not None else Path.cwd())
        self.provider_index = provider_index
        self.enable_fallback = enable_fallback
        if permission_policy not in VALID_PERMISSION_POLICIES:
            raise ValueError(
                f"Invalid permission_policy '{permission_policy}', "
                f"must be one of: {', '.join(sorted(VALID_PERMISSION_POLICIES))}"
            )
        # 权限策略必须显式：非交互场景没有 UI 可以回答 PermissionRequest，
        # 必须给出确定性答案，否则 Agent 会在 await future 上永久挂起。
        self.permission_policy = permission_policy
        self.timeout_seconds = timeout_seconds
        # 仅用于实时进度观测（不影响执行语义）。
        self.on_progress = on_progress
        self.permission_decisions: list[dict[str, Any]] = []
        # 跨轮累计的观测数据：多轮 case 的 tool 调用与回答都必须可审计，
        # 不能只保留最后一轮（否则 ERROR_001 这类跨轮验证无法进行）。
        self.tool_events: list[dict[str, Any]] = []
        self.transcript: list[dict[str, str]] = []
        self.attachments: list[dict[str, Any]] = []
        # 仅用于耗时诊断的观测计数器（不改变任何执行逻辑）。
        self.metrics: dict[str, Any] = {
            "agent_turn_count": 0,
            "llm_call_count": 0,
            "tool_call_count": 0,
            "permission_request_count": 0,
            "tool_seconds": 0.0,
            "session_seconds": 0.0,
            "last_event": "",
            "pending_tool": "",
            "timeout_stage": "",
        }
        # 复用同一个会话对象，多轮上下文（Context/Session 类用例）才真正生效。
        self.conversation = ConversationManager()
        self.config = self._load_config()
        self.provider = self.config.providers[provider_index] if self.config.providers else None
        self.session_manager = SessionManager(self.work_dir)
        self.session = self.session_manager.create()

        if self.provider is None:
            self.client = None
            self.agent = None
            return

        try:
            self.client = create_client(self.provider)
            # Benchmark 隔离：Bash 的 cwd 固定为 case workspace，并开启启发式越界预检，
            # 防止 Agent 通过 `cd`/绝对路径读到 repo 里的 benchmark/cases、evaluator 源码
            # 或其他 Case 的 workspace。生产环境默认不开启（见 wendream/tools/__init__.py）。
            self.registry = create_default_registry(
                work_dir=self.work_dir, restrict_bash_to_workspace=True
            )
            self.checker = PermissionChecker(
                detector=DangerousCommandDetector(),
                sandbox=PathSandbox(self.work_dir),
                rule_engine=RuleEngine(
                    user_rules_path=Path.home() / ".wendream" / "permissions.yaml",
                    project_rules_path=Path(self.work_dir) / ".wendream" / "permissions.yaml",
                    local_rules_path=Path(self.work_dir) / ".wendream" / "permissions.local.yaml",
                ),
                mode=PermissionMode.DEFAULT,
                observer=self._on_permission_decision,
            )
            self.instructions = load_instructions(self.work_dir)
            self.agent = Agent(
                client=self.client,
                registry=self.registry,
                protocol=self.provider.protocol,
                work_dir=self.work_dir,
                permission_checker=self.checker,
                context_window=self.provider.get_context_window(),
                instructions_content=self.instructions,
            )
        except Exception:
            self.client = None
            self.agent = None

    def _load_config(self):
        try:
            return load_config()
        except Exception:
            return type("Cfg", (), {"providers": []})()

    def _on_permission_decision(self, tool, arguments, decision) -> None:
        """接收 PermissionChecker 产生的**真实结构化决策**（只读观察者）。

        不解析任何自然语言文本；decision 直接来自 PermissionChecker.check()。
        """
        try:
            from wendream.permissions.rules import extract_content

            content = extract_content(tool.name, arguments)
        except Exception:
            content = ""
        record = {
            "source": "permission_checker",
            "tool_name": getattr(tool, "name", ""),
            "operation": str(getattr(tool, "category", "")),
            "path": content,
            "decision": str(getattr(decision, "effect", "")),
            "reason": str(getattr(decision, "reason", "")),
            "rule": str(getattr(decision, "rule", "") or ""),
            "timestamp": time.time(),
        }
        self.permission_decisions.append(record)
        if record["decision"] == "deny":
            self.metrics["permission_deny_count"] = self.metrics.get("permission_deny_count", 0) + 1

    def _infrastructure_error(self, message: str) -> dict[str, Any]:
        return {
            "status": "INFRASTRUCTURE_ERROR",
            "answer": "",
            "final_response": "",
            "tool_events": list(getattr(self, "tool_events", [])),
            "errors": [message],
            "session_id": self.session.session_id,
            "fallback": False,
            "infrastructure_error": True,
            "timeout": False,
            "permission_decisions": list(getattr(self, "permission_decisions", [])),
            "metrics": dict(getattr(self, "metrics", {})),
            "transcript": list(getattr(self, "transcript", [])),
            "attachments": list(getattr(self, "attachments", [])),
        }

    def _resolve_permission(self, event: PermissionRequest) -> None:
        """非交互场景下必须立即给 PermissionRequest 一个确定性答案。

        对应 CLI `-p` 模式的同类处理（那边固定 ALLOW）。如果不回应，
        Agent 会在 ``await future`` 上永久阻塞——这正是 benchmark 卡死的根因。
        """
        mapping = {
            "allow": PermissionResponse.ALLOW,
            "allow_always": PermissionResponse.ALLOW_ALWAYS,
            "deny": PermissionResponse.DENY,
        }
        decision = mapping[self.permission_policy]
        self.permission_decisions.append(
            {
                "tool_name": event.tool_name,
                "description": event.description,
                "decision": decision.value,
                "policy": self.permission_policy,
            }
        )
        if not event.future.done():
            event.future.set_result(decision)

    @staticmethod
    def load_attachments(attachments: list[dict[str, Any]] | None, base_dir: str | Path | None = None):
        """把 case 里的附件声明转换成 WenDream 真实的 ImageBlock / DocumentBlock。

        附件必须经过项目自身的 multimodal 结构（conversation.ImageBlock /
        DocumentBlock），由 serialization 生成 image_url / file_data，
        不允许 Benchmark 自己解析图片内容。
        """
        from wendream.conversation import DocumentBlock, ImageBlock

        images: list[Any] = []
        documents: list[Any] = []
        resolved: list[dict[str, Any]] = []
        for item in attachments or []:
            raw_path = item.get("path")
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute() and base_dir is not None:
                path = Path(base_dir) / raw_path
            if not path.exists():
                resolved.append({"path": str(path), "kind": item.get("type", "image"), "missing": True})
                continue
            payload = base64.b64encode(path.read_bytes()).decode()
            kind = (item.get("type") or "image").lower()
            media_type = item.get("media_type") or (
                "application/pdf" if path.suffix.lower() == ".pdf" else "image/png"
            )
            if kind == "document":
                documents.append(DocumentBlock(media_type=media_type, data=payload, name=path.name))
            else:
                images.append(ImageBlock(media_type=media_type, data=payload))
            resolved.append(
                {
                    "path": str(path),
                    "kind": kind,
                    "media_type": media_type,
                    "size_bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                    "missing": False,
                }
            )
        return images, documents, resolved

    def _emit_progress(self) -> None:
        """把当前阶段与计数推给调用方（仅观测用途）。"""
        if self.on_progress is None:
            return
        pending = self.metrics.get("pending_tool") or ""
        last = self.metrics.get("last_event") or ""
        if pending:
            stage = f"tool:{pending}"
        elif last == "PermissionRequest":
            stage = "permission"
        elif last in ("StreamText", "ThinkingText"):
            stage = "llm_stream"
        else:
            stage = "agent"
        try:
            self.on_progress({"stage": stage, "metrics": dict(self.metrics)})
        except Exception:
            pass

    async def run_prompt(
        self,
        prompt: str,
        *,
        workspace: str | Path | None = None,
        attachments: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if self.agent is None:
            return self._infrastructure_error("WenDream adapter could not initialize a real agent.")

        if workspace is not None:
            self.work_dir = str(Path(workspace))

        final_text: list[str] = []
        errors: list[str] = []
        images, documents, resolved_attachments = self.load_attachments(attachments)
        self.attachments = resolved_attachments

        async def drive() -> None:
            self.conversation.add_user_message(prompt, images=images, documents=documents)
            async for event in self.agent.run(self.conversation):
                self.metrics["last_event"] = type(event).__name__
                if isinstance(event, StreamText):
                    final_text.append(event.text)
                elif isinstance(event, ThinkingText):
                    pass
                elif isinstance(event, ToolUseEvent):
                    self.metrics["tool_call_count"] += 1
                    self.metrics["pending_tool"] = event.tool_name
                    self.tool_events.append(
                        {
                            "tool_name": event.tool_name,
                            "tool_id": event.tool_id,
                            "arguments": event.arguments,
                        }
                    )
                elif isinstance(event, ToolResultEvent):
                    self.metrics["tool_seconds"] += float(event.elapsed or 0.0)
                    self.metrics["pending_tool"] = ""
                    self.tool_events.append(
                        {
                            "tool_name": event.tool_name,
                            "tool_id": event.tool_id,
                            "result": event.output,
                            "is_error": event.is_error,
                            "elapsed": round(float(event.elapsed or 0.0), 3),
                        }
                    )
                elif isinstance(event, PermissionRequest):
                    self.metrics["permission_request_count"] += 1
                    self._resolve_permission(event)
                elif isinstance(event, UsageEvent):
                    self.metrics["llm_call_count"] += 1
                elif isinstance(event, TurnComplete):
                    self.metrics["agent_turn_count"] += 1
                elif isinstance(event, ErrorEvent):
                    errors.append(event.message)
                self._emit_progress()

        effective_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        try:
            if effective_timeout and effective_timeout > 0:
                await asyncio.wait_for(drive(), timeout=effective_timeout)
            else:
                await drive()
        except (asyncio.TimeoutError, TimeoutError):
            # 超时是真实的运行时故障，必须如实上报，不能伪装成正常结果。
            # 同时标记卡住发生在哪个阶段，便于诊断"模型慢"还是"在等某个东西"。
            if self.metrics.get("pending_tool"):
                stage = "tool"
            elif self.metrics.get("last_event") == "PermissionRequest":
                stage = "permission"
            elif self.metrics.get("last_event") in ("StreamText", "ThinkingText", "UsageEvent", ""):
                stage = "llm"
            else:
                stage = "llm"
            self.metrics["timeout_stage"] = stage
            self.transcript.append({"role": "assistant", "content": "".join(final_text)})
            return {
                "status": "TIMEOUT",
                "answer": "".join(final_text),
                "final_response": "".join(final_text),
                "tool_events": list(self.tool_events),
                "errors": [f"Agent turn exceeded {effective_timeout}s timeout"],
                "session_id": self.session.session_id,
                "fallback": False,
                "infrastructure_error": False,
                "timeout": True,
                "timeout_stage": stage,
                "permission_decisions": list(self.permission_decisions),
                "metrics": dict(self.metrics),
                "transcript": list(self.transcript),
                "attachments": list(getattr(self, "attachments", [])),
            }
        except Exception as exc:
            message = str(exc)
            # 模型/Provider 明确不支持文档块（PDF）时，属于能力边界，不是 infra 故障。
            has_documents = any(
                a.get("kind") == "document" for a in getattr(self, "attachments", [])
            )
            if has_documents and ("file must have a file_id or file_data" in message or "file_data" in message):
                return {
                    "status": "SKIPPED",
                    "skip_reason": "model_does_not_support_document_input",
                    "answer": "",
                    "final_response": "",
                    "tool_events": list(self.tool_events),
                    "errors": [message],
                    "session_id": self.session.session_id,
                    "fallback": False,
                    "infrastructure_error": False,
                    "timeout": False,
                    "document_unsupported": True,
                    "permission_decisions": list(self.permission_decisions),
                    "metrics": dict(self.metrics),
                    "transcript": list(self.transcript),
                    "attachments": list(getattr(self, "attachments", [])),
                }
            result = self._infrastructure_error(message)
            result["tool_events"] = list(self.tool_events)
            result["permission_decisions"] = list(self.permission_decisions)
            return result

        content = "".join(final_text).strip()
        self.transcript.append({"role": "user", "content": prompt})
        self.transcript.append({"role": "assistant", "content": content})
        session_start = time.perf_counter()
        self.session.append(Message(role="user", content=prompt))
        self.session.append(Message(role="assistant", content=content))
        self.metrics["session_seconds"] += time.perf_counter() - session_start
        return {
            "status": "OK",
            "answer": content,
            "final_response": content,
            "tool_events": list(self.tool_events),
            "errors": errors,
            "session_id": self.session.session_id,
            "fallback": False,
            "infrastructure_error": False,
            "timeout": False,
            "permission_decisions": list(self.permission_decisions),
            "metrics": dict(self.metrics),
            "transcript": list(self.transcript),
            "attachments": list(getattr(self, "attachments", [])),
        }

    def close(self) -> None:
        """释放 SessionManager.create() 打开的 jsonl 文件句柄。

        不关闭会导致 Windows 上 workspace 目录无法删除（文件被占用），
        进而让下一轮 benchmark 的 rmtree 失败。仅做资源回收，不改变执行语义。
        """
        session = getattr(self, "session", None)
        # Session 内部持有的文件句柄字段是 _file（不是 file）。
        handle = getattr(session, "_file", None)
        if handle is not None and not getattr(handle, "closed", True):
            try:
                handle.close()
            except Exception:  # pragma: no cover - 资源回收尽力而为
                pass

    def run_prompt_sync(
        self,
        prompt: str,
        *,
        workspace: str | Path | None = None,
        attachments: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(
            self.run_prompt(
                prompt,
                workspace=workspace,
                attachments=attachments,
                timeout_seconds=timeout_seconds,
            )
        )

    @staticmethod
    def detect_vision_capability(provider) -> dict[str, Any]:
        """真正验证 provider 是否具备视觉能力。

        判定标准不是"API 接受 image_url"，而是让模型对一张**内容确定**的图片
        （纯红色方块）作答，并要求回答里出现红色语义。失败即判定不支持。
        """
        from PIL import Image  # 仅在探测时使用，属于 benchmark 侧依赖

        import io

        probe = {"supported": False, "reason": "", "probe_answer": "", "probe_error": ""}
        try:
            img = Image.new("RGB", (96, 96), (220, 20, 20))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            payload = base64.b64encode(buf.getvalue()).decode()

            from wendream.conversation import ConversationManager, ImageBlock

            client = create_client(provider)
            conversation = ConversationManager()
            conversation.add_user_message(
                "这张图片的主要颜色是什么？只回答颜色词。",
                images=[ImageBlock(media_type="image/png", data=payload)],
            )

            async def _ask() -> str:
                chunks: list[str] = []
                async for event in client.stream(conversation, system=""):
                    text = getattr(event, "text", None)
                    if type(event).__name__ == "TextDelta" and text:
                        chunks.append(text)
                return "".join(chunks)

            answer = asyncio.run(asyncio.wait_for(_ask(), timeout=90))
            probe["probe_answer"] = answer
            haystack = answer.lower()
            probe["supported"] = any(token in haystack for token in ("红", "red", "crimson"))
            if not probe["supported"]:
                probe["reason"] = "probe_answer_did_not_confirm_vision"
        except Exception as exc:  # pragma: no cover - 依赖外部服务
            probe["reason"] = "probe_failed"
            probe["probe_error"] = f"{type(exc).__name__}: {exc}"
        return probe
