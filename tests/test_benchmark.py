import asyncio
import json
import time
from pathlib import Path

import pytest

from benchmark.adapters.wendream_adapter import WenDreamAdapter
from benchmark.evaluator import (
    composite,
    contains,
    file_content,
    json_field,
    set_match,
)
from benchmark.runner import BenchmarkRunner
from benchmark.runner_validated import ValidatedBenchmarkRunner, task_success_rate
from wendream.agent import PermissionRequest, PermissionResponse, StreamText
from wendream.tools.bash import _interpret_exit_code


def test_basic_evaluators():
    assert contains("hello world", "world")
    assert contains("hello world", ["hello", "world"]) is True
    assert json_field({"name": "alice"}, "name", "alice")
    assert set_match({"a", "b"}, {"b", "a"})
    assert file_content("hello\nworld\n", "world")
    assert composite("alpha beta", ["contains:alpha", "contains:beta"]) is True


def test_adapter_reports_infrastructure_error_without_real_agent(tmp_path):
    adapter = WenDreamAdapter(work_dir=tmp_path, enable_fallback=False)
    # 显式移除 agent，验证“没有真实运行时”时不会伪造成功结果，
    # 也不依赖外部 provider 的账号/网络状态。
    adapter.agent = None
    result = adapter.run_prompt_sync("read a file")
    assert result["status"] == "INFRASTRUCTURE_ERROR"
    assert result["infrastructure_error"] is True
    assert result["final_response"] == ""


def test_runner_does_not_inject_case_id_answers(tmp_path, monkeypatch):
    case = {
        "id": "REAL_001",
        "prompt": "What is 2 + 2?",
        "checks": [{"type": "contains", "expected": "4"}],
    }

    def fake_run(self, prompt, *, workspace=None):
        return {
            "status": "OK",
            "final_response": "4",
            "tool_events": [],
            "latency_seconds": 0.01,
            "session_id": "sess-1",
            "infrastructure_error": False,
            "errors": [],
        }

    monkeypatch.setattr(WenDreamAdapter, "run_prompt_sync", fake_run)
    runner = BenchmarkRunner(base_dir=tmp_path)
    result = runner.run_case_definition(case)
    assert result["case_id"] == "REAL_001"
    assert result["status"] == "PASSED"
    assert result["answer"] == "4"
    assert "fallback_used" not in result


def test_bash_exit_code_semantics():
    assert _interpret_exit_code("grep foo missing.txt", 1) is False
    assert _interpret_exit_code("python -c \"import sys; sys.exit(1)\"", 1) is True


def test_case_catalog_has_expected_distribution():
    runner = BenchmarkRunner(base_dir=Path(__file__).resolve().parents[1])
    cases = runner.load_cases()
    assert len(cases) == 30
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    counts = {}
    for case in cases:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    assert counts == {"Context": 4, "File Operations": 6, "Tool Calling": 6, "Information Processing": 4, "Vision": 4, "Session": 3, "Error Recovery": 3}
    for case in cases:
        assert "checks" in case and case["checks"]
        assert "prompt" in case
    assert any(case["category"] == "Vision" for case in cases)
    assert any(case["category"] == "Session" for case in cases)


class _StubAgent:
    """可控的 Agent 替身，仅用于验证 adapter 的事件处理与超时逻辑。"""

    def __init__(self, factory):
        self._factory = factory

    def run(self, conversation):
        return self._factory(conversation)


def test_adapter_answers_permission_request_instead_of_hanging(tmp_path):
    """回归：Agent 会 await PermissionRequest.future，adapter 必须回应，否则永久挂起。"""
    seen = {}

    async def factory(conversation):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        yield PermissionRequest(tool_name="WriteFile", description="write outside sandbox", future=future)
        seen["decision"] = await asyncio.wait_for(future, timeout=2)
        yield StreamText(text="done")

    adapter = WenDreamAdapter(work_dir=tmp_path, permission_policy="deny", timeout_seconds=10)
    adapter.agent = _StubAgent(factory)

    result = adapter.run_prompt_sync("test prompt")

    assert seen.get("decision") == PermissionResponse.DENY
    assert result["status"] == "OK"
    assert result["timeout"] is False
    assert result["permission_decisions"][0]["decision"] == "deny"


def test_adapter_times_out_instead_of_hanging(tmp_path):
    """回归：模型/工具卡住时必须超时返回 TIMEOUT，而不是无限等待。"""

    async def factory(conversation):
        yield StreamText(text="partial")
        await asyncio.sleep(600)

    adapter = WenDreamAdapter(work_dir=tmp_path, timeout_seconds=0.3)
    adapter.agent = _StubAgent(factory)

    started = time.perf_counter()
    result = adapter.run_prompt_sync("test prompt")
    elapsed = time.perf_counter() - started

    assert result["status"] == "TIMEOUT"
    assert result["timeout"] is True
    assert elapsed < 5


def test_runner_reports_timeout_status(tmp_path, monkeypatch):
    class StubAdapter:
        def __init__(self, **kwargs):
            pass

        def run_prompt_sync(self, prompt, *, workspace=None):
            return {
                "status": "TIMEOUT",
                "final_response": "",
                "tool_events": [],
                "errors": ["Agent turn exceeded timeout"],
                "session_id": "sess-timeout",
                "infrastructure_error": False,
                "timeout": True,
                "permission_decisions": [],
            }

    monkeypatch.setattr("benchmark.runner.WenDreamAdapter", StubAdapter)
    runner = BenchmarkRunner(base_dir=tmp_path)
    case = {
        "id": "T_001",
        "category": "Tool Calling",
        "prompt": "do something",
        "checks": [{"type": "contains", "expected": "never"}],
    }

    result = runner.run_case_definition(case)

    assert result["status"] == "TIMEOUT"
    assert result["infrastructure_error"] is False
    assert "timeout" in (result["error"] or "").lower()


# ---------------------------------------------------------------------------
# v1_validated：评测本身必须可信
# ---------------------------------------------------------------------------

def _validated_runner() -> ValidatedBenchmarkRunner:
    return ValidatedBenchmarkRunner(base_dir=Path(__file__).resolve().parents[1])


def test_validated_catalog_has_expected_structure():
    cases = _validated_runner().load_cases()
    assert len(cases) == 30
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    counts = {}
    for case in cases:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    assert counts == {
        "Context": 4,
        "File Operations": 6,
        "Tool Calling": 6,
        "Information Processing": 4,
        "Vision": 4,
        "Session": 3,
        "Error Recovery": 3,
    }


def test_validated_special_cases_are_marked():
    cases = {c["id"]: c for c in _validated_runner().load_cases()}

    assert cases["SESSION_001"].get("requires_disk_resume") is True
    assert cases["SESSION_002"].get("requires_session_isolation") is True
    assert cases["SESSION_003"].get("expect_skipped") is True
    assert "Web/Remote" in cases["SESSION_003"]["skip_reason"]

    fixture_dir = Path(__file__).resolve().parents[1] / "benchmark" / "fixtures"
    for vid in ("VISION_001", "VISION_002", "VISION_003", "VISION_004"):
        attachments = cases[vid].get("attachments") or []
        assert attachments, f"{vid} must declare a real attachment"
        for item in attachments:
            assert (fixture_dir / item["path"]).exists(), f"missing fixture {item['path']}"

    assert cases["ERROR_002"].get("permission_policy") == "deny"
    rules = cases["ERROR_002"].get("permission_rules") or []
    assert rules and all(r["effect"] == "deny" for r in rules)


def test_error_001_uses_structured_checks():
    cases = {c["id"]: c for c in _validated_runner().load_cases()}
    checks = cases["ERROR_001"]["checks"]
    types = [c["type"] for c in checks]
    assert "tool_error_for" in types
    assert "transcript_contains_any" in types
    # 不能只依赖最终自然语言里出现 "not found"
    assert not any(
        c["type"] == "contains" and str(c.get("expected", "")).lower() == "not found"
        for c in checks
    )


def test_task_success_rate_formula():
    # TIMEOUT 计入分母；SKIPPED / INFRA / EVALUATOR / DESIGN 不计入
    assert abs(task_success_rate(23, 2, 1) - 88.46) < 0.05
    assert task_success_rate(1, 1, 0) == 50.0
    assert task_success_rate(0, 0, 0) == 0.0


def test_artifactize_externalizes_large_tool_output(tmp_path):
    runner = ValidatedBenchmarkRunner(base_dir=tmp_path)
    big = "X" * (150 * 1024)
    events = [{"tool_name": "Bash", "tool_id": "call_1", "result": big, "is_error": False}]
    out = runner._artifactize("FILE_999", events)
    payload = out[0]["result"]
    assert isinstance(payload, dict) and payload["externalized"] is True
    assert Path(payload["artifact_path"]).exists()
    assert payload["size_bytes"] == len(big.encode("utf-8"))
    assert len(payload["sha256"]) == 64
    assert payload["preview_head"] and payload["preview_tail"]


def test_latest_json_is_summary_only(tmp_path):
    runner = ValidatedBenchmarkRunner(base_dir=tmp_path)
    runner.results_dir.mkdir(parents=True, exist_ok=True)
    runner.traces_dir.mkdir(parents=True, exist_ok=True)

    results = [
        {
            "case_id": "CTX_001",
            "category": "CONTEXT",
            "status": "PASSED",
            "latency_seconds": 1.0,
            "counts": {"llm_call_count": 1, "tool_call_count": 0, "permission_request_count": 0},
            "timing": {"total_seconds": 1.0},
            "session_id": "s1",
            "provider": "p",
            "model": "m",
            "error": None,
            "tool_events": [{"tool_name": "Bash", "result": "Y" * 1000}],
            "answer": "ok",
        },
        {
            "case_id": "VISION_001",
            "category": "VISION",
            "status": "SKIPPED",
            "latency_seconds": 0.0,
            "counts": {},
            "timing": {"total_seconds": 0.0},
            "session_id": None,
            "provider": "p",
            "model": "m",
            "error": "model_does_not_support_vision",
            "tool_events": [],
            "answer": "",
        },
    ]
    runner._write_reports(results, 1.0)

    rows = json.loads((runner.results_dir / "latest.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    for row in rows:
        assert "tool_events" not in row
        assert "answer" not in row
        assert "trace_path" in row
    report = (runner.results_dir / "report.md").read_text(encoding="utf-8")
    assert "PASSED / (PASSED + FAILED + TIMEOUT)" in report
    assert "92.0%" not in report


def test_every_case_writes_trace_even_when_skipped(tmp_path):
    runner = ValidatedBenchmarkRunner(base_dir=Path(__file__).resolve().parents[1])
    runner.results_dir = tmp_path / "results"
    runner.traces_dir = runner.results_dir / "traces"
    runner.artifacts_dir = runner.results_dir / "artifacts"

    cases = {c["id"]: c for c in runner.load_cases()}
    result = runner.run_case(cases["SESSION_003"])

    assert result["status"] == "SKIPPED"
    trace_path = runner.traces_dir / "SESSION_003.json"
    assert trace_path.exists()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["status"] == "SKIPPED"
    assert trace["detected_web_handler"]
    assert trace["missing_interface"]


def test_permission_denied_and_file_checks(tmp_path):
    from benchmark.evaluator import evaluate_check

    (tmp_path / "protected_target.txt").write_text("ORIGINAL\n", encoding="utf-8")
    ctx = {
        "permission_decisions": [{"tool_name": "WriteFile", "decision": "deny"}],
        "tool_events": [],
    }
    assert evaluate_check("", {"type": "permission_denied"}, workspace=str(tmp_path), context=ctx) is True
    assert evaluate_check(
        "", {"type": "file_not_contains", "path": "protected_target.txt", "expected": "SHOULD_NOT_EXIST"},
        workspace=str(tmp_path), context=ctx,
    ) is True


def test_ordered_and_session_checks():
    from benchmark.evaluator import evaluate_check

    assert evaluate_check(
        "顺序为 Input → Agent → Tool → Result", {"type": "ordered", "expected": ["Input", "Agent", "Tool", "Result"]}
    )
    assert not evaluate_check(
        "Tool Agent Input Result", {"type": "ordered", "expected": ["Input", "Agent", "Tool", "Result"]}
    )
    ctx = {"session": {"agent_ids_differ": True, "resume_called": True}}
    assert evaluate_check("", {"type": "session_field", "field": "resume_called", "expected": True}, context=ctx)
    assert not evaluate_check("", {"type": "session_field", "field": "agent_ids_differ", "expected": False}, context=ctx)


# ---------------------------------------------------------------------------
# Session 超时分类 / 阶段观测
# ---------------------------------------------------------------------------

def test_session_stage_timeout_is_timeout_not_infrastructure(tmp_path, monkeypatch):
    import benchmark.session_flows as sf
    from wendream.agent import StreamText

    monkeypatch.setattr(sf, "TURN_TIMEOUT", 0.2)

    class HangAgent:
        async def run(self, conversation):
            yield StreamText(text="thinking")
            await asyncio.sleep(5)

    recorder = sf.FlowRecorder(emit=lambda *_: None)
    with pytest.raises(sf.SessionStageTimeout) as info:
        asyncio.run(sf._turn(HangAgent(), sf.ConversationManager(), "hi", recorder, "query_session_a"))

    assert info.value.stage == "query_session_a"
    assert info.value.turn_index == 1
    assert recorder.stage_failed == "query_session_a"

    payload = sf._timeout_payload(recorder, info.value, time.perf_counter())
    assert payload["status"] == "TIMEOUT"
    assert payload["timeout_stage"] == "query_session_a"
    assert "status" in payload and payload["status"] != "INFRASTRUCTURE_ERROR"


def test_session_recorder_captures_stages_and_counts():
    import benchmark.session_flows as sf
    from wendream.agent import StreamText, ToolUseEvent, UsageEvent

    class CountingAgent:
        async def run(self, conversation):
            yield UsageEvent(input_tokens=10, output_tokens=5)
            yield ToolUseEvent(tool_name="ReadFile", tool_id="t1", arguments={"file_path": "a.txt"})
            yield StreamText(text="done")

    recorder = sf.FlowRecorder(emit=lambda *_: None)
    reply = asyncio.run(
        sf._turn(CountingAgent(), sf.ConversationManager(), "hi", recorder, "seed_session_a")
    )

    assert reply == "done"
    assert recorder.llm_call_count == 1
    assert recorder.tool_call_count == 1
    assert recorder.last_completed_stage == "seed_session_a"
    stages = {s["stage"] for s in recorder.stages}
    assert "seed_session_a" in stages
    assert all("latency" in s for s in recorder.stages)


# ---------------------------------------------------------------------------
# Permission：结构化证据，不做字符串推断
# ---------------------------------------------------------------------------

def test_permission_deny_evidence_is_structured(tmp_path):
    from wendream.permissions.checker import PermissionChecker
    from wendream.permissions.dangerous import DangerousCommandDetector
    from wendream.permissions.modes import PermissionMode
    from wendream.permissions.rules import RuleEngine
    from wendream.permissions.sandbox import PathSandbox

    rules_dir = tmp_path / ".wendream"
    rules_dir.mkdir()
    (rules_dir / "permissions.yaml").write_text(
        '- rule: "WriteFile(*protected_target.txt)"\n  effect: deny\n', encoding="utf-8"
    )

    captured = []
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(tmp_path)),
        rule_engine=RuleEngine(project_rules_path=rules_dir / "permissions.yaml"),
        mode=PermissionMode.DEFAULT,
        observer=lambda tool, args, decision: captured.append((tool.name, decision.effect, decision.rule)),
    )

    class FakeWriteTool:
        name = "WriteFile"
        category = "write"

    decision = checker.check(
        FakeWriteTool(), {"file_path": str(tmp_path / "protected_target.txt"), "content": "x"}
    )

    assert decision.effect == "deny"
    assert decision.rule == "WriteFile(*protected_target.txt)"
    assert captured == [("WriteFile", "deny", "WriteFile(*protected_target.txt)")]

    # evaluator 必须认可这条结构化证据
    from benchmark.evaluator import evaluate_check

    ctx = {
        "permission_decisions": [
            {
                "source": "permission_checker",
                "tool_name": "WriteFile",
                "decision": "deny",
                "path": str(tmp_path / "protected_target.txt"),
            }
        ]
    }
    assert evaluate_check(
        "",
        {"type": "permission_denied", "path_contains": "protected_target.txt", "source": "permission_checker"},
        context=ctx,
    )


def test_no_string_based_permission_inference(tmp_path):
    """工具结果里出现 'Permission denied' 不等于 PermissionChecker 给出了 decision。"""
    from wendream.agent import StreamText, ToolResultEvent

    class StubAgent:
        def run(self, conversation):
            async def gen():
                yield ToolResultEvent(
                    tool_id="t1",
                    tool_name="WriteFile",
                    output="Permission denied: 权限规则拒绝",
                    is_error=True,
                    elapsed=0.01,
                )
                yield StreamText(text="blocked")

            return gen()

    adapter = WenDreamAdapter(work_dir=tmp_path, permission_policy="deny", timeout_seconds=10)
    adapter.agent = StubAgent()
    result = adapter.run_prompt_sync("write it")

    assert result["status"] == "OK"
    assert result["permission_decisions"] == []
