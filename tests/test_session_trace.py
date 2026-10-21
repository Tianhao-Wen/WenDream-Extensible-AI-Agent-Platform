"""Session 流程的 trace 观测测试（Phase 2A.1）。

目标：session 类 Case 的 trace 必须和普通 Case 一样含有结构化工具调用，
并且 ``counts.tool_call_count`` 与实际记录到的工具调用条数一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.session_flows import FlowRecorder  # noqa: E402
from wendream.agent import ToolResultEvent, ToolUseEvent, UsageEvent  # noqa: E402


def _use(tool_id: str, name: str, args: dict) -> ToolUseEvent:
    return ToolUseEvent(tool_name=name, tool_id=tool_id, arguments=args)


def _result(tool_id: str, name: str, output: str, is_error: bool = False, elapsed: float = 0.5):
    return ToolResultEvent(tool_id=tool_id, tool_name=name, output=output, is_error=is_error, elapsed=elapsed)


def test_use_and_result_are_counted_once():
    recorder = FlowRecorder(emit=lambda _: None)
    recorder.start("seed_session_a")
    recorder.note_event(UsageEvent(input_tokens=1, output_tokens=1))
    recorder.note_event(_use("c1", "Glob", {"pattern": "**/*"}))
    recorder.note_event(_result("c1", "Glob", "a.txt\nb.txt"))
    recorder.end()

    summary = recorder.summary()
    assert summary["llm_call_count"] == 1
    # 一对 (use, result) 只能算 1 次工具调用
    assert summary["tool_call_count"] == 1
    assert len(summary["tool_calls"]) == 1 == summary["tool_call_count"]


def test_tool_call_record_is_structured():
    recorder = FlowRecorder(emit=lambda _: None)
    recorder.start("query_session_a")
    recorder.note_event(_use("c1", "ReadFile", {"file_path": "local.txt"}))
    recorder.note_event(_result("c1", "ReadFile", "hello", is_error=False, elapsed=0.123))
    recorder.end()

    call = recorder.summary()["tool_calls"][0]
    assert call["sequence"] == 1
    assert call["stage"] == "query_session_a"
    assert call["tool_name"] == "ReadFile"
    assert call["tool_id"] == "c1"
    assert call["arguments"] == {"file_path": "local.txt"}
    assert call["result"] == "hello"
    assert call["is_error"] is False
    assert call["elapsed"] == pytest.approx(0.123)


def test_multiple_calls_keep_order_and_stage():
    recorder = FlowRecorder(emit=lambda _: None)
    recorder.start("seed_session_b")
    recorder.note_event(_use("c1", "Glob", {"pattern": "*"}))
    recorder.note_event(_result("c1", "Glob", "x"))
    recorder.end()
    recorder.start("query_session_b")
    recorder.note_event(_use("c2", "Bash", {"command": "dir"}))
    recorder.end()

    calls = recorder.summary()["tool_calls"]
    assert [c["sequence"] for c in calls] == [1, 2]
    assert [c["stage"] for c in calls] == ["seed_session_b", "query_session_b"]


def test_error_result_kept():
    recorder = FlowRecorder(emit=lambda _: None)
    recorder.start("seed_session_a")
    recorder.note_event(_use("c1", "Bash", {"command": "ls -la"}))
    recorder.note_event(_result("c1", "Bash", "'ls' is not recognized", is_error=True, elapsed=0.02))
    recorder.end()

    call = recorder.summary()["tool_calls"][0]
    assert call["is_error"] is True
    assert "not recognized" in call["result"]


def test_result_without_use_event_still_recorded():
    """真实运行时理论上都有 ToolUseEvent，但绝不因为缺失就丢信息。"""
    recorder = FlowRecorder(emit=lambda _: None)
    recorder.start("seed_session_a")
    recorder.note_event(_result("orphan", "Grep", "no matches"))
    recorder.end()

    calls = recorder.summary()["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "Grep"
    assert calls[0]["result"] == "no matches"


def test_permission_decisions_recorded():
    recorder = FlowRecorder(emit=lambda _: None)

    class Tool:
        name = "ReadFile"
        category = "read"

    class Decision:
        effect = "deny"
        reason = "路径沙箱拦截: 超出沙箱范围"
        rule = ""

    recorder.note_permission(Tool(), {"file_path": "../x.txt"}, Decision())
    summary = recorder.summary()
    assert summary["permission_denied_count"] == 1
    assert summary["permission_decisions"][0]["decision"] == "deny"
    assert summary["permission_decisions"][0]["path"] == "../x.txt"
