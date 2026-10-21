"""WenDream Benchmark v1_validated runner.

与 v1 runner 的区别（只改评测框架，不改 Agent）：
  - 7 种状态：PASSED / FAILED / TIMEOUT / SKIPPED / INFRASTRUCTURE_ERROR /
    EVALUATOR_ERROR / BENCHMARK_DESIGN_ERROR
  - Task Success Rate 唯一口径：PASSED / (PASSED + FAILED + TIMEOUT)
  - latest.json 只存摘要；超大工具输出外置到 artifacts/
  - 每个 case 都生成 trace（含 SKIPPED）
  - 支持真实附件（Vision）、磁盘 Session 恢复、Permission rule 注入
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import statistics
import time
from pathlib import Path
from typing import Any

from benchmark.adapters.wendream_adapter import DEFAULT_TURN_TIMEOUT_SECONDS, WenDreamAdapter
from benchmark.evaluator import evaluate_check
from benchmark import session_flows

VERSION = "v1_validated"
VALID_STATUSES = {
    "PASSED",
    "FAILED",
    "TIMEOUT",
    "SKIPPED",
    "INFRASTRUCTURE_ERROR",
    "EVALUATOR_ERROR",
    "BENCHMARK_DESIGN_ERROR",
}
ARTIFACT_THRESHOLD_BYTES = 100 * 1024
PREVIEW_CHARS = 500


def task_success_rate(passed: int, failed: int, timeout: int) -> float:
    """唯一的 Task Success Rate 口径：PASSED / (PASSED + FAILED + TIMEOUT)。

    SKIPPED / INFRASTRUCTURE_ERROR / EVALUATOR_ERROR / BENCHMARK_DESIGN_ERROR
    一律不进入分母（但报告中必须单独列出）。
    """
    denominator = passed + failed + timeout
    return (passed / denominator * 100) if denominator else 0.0


class ValidatedBenchmarkRunner:
    def __init__(
        self,
        base_dir: str | Path | None = None,
        cases_path: str | Path | None = None,
        results_dir_name: str = VERSION,
        case_timeout_seconds: float | None = None,
        show_progress: bool = True,
    ):
        if base_dir is None:
            candidates = [Path(__file__).resolve().parent]
        else:
            raw = Path(base_dir)
            candidates = [raw] if raw.name == "benchmark" else [raw / "benchmark", raw]
        existing = next((p for p in candidates if (p / "cases").exists()), None)
        if existing is None:
            existing = candidates[0]
            existing.mkdir(parents=True, exist_ok=True)
        self.benchmark_dir = existing
        self.cases_path = (
            Path(cases_path)
            if cases_path is not None
            else self.benchmark_dir / "cases" / "benchmark_v1_validated.json"
        )
        self.fixtures_dir = self.benchmark_dir / "fixtures"
        self.workspaces_dir = self.benchmark_dir / "workspaces"
        self.results_dir = self.benchmark_dir / "results" / results_dir_name
        self.traces_dir = self.results_dir / "traces"
        self.artifacts_dir = self.results_dir / "artifacts"
        self.case_timeout_seconds = case_timeout_seconds
        self.show_progress = show_progress
        self._vision_probe: dict[str, Any] | None = None
        self._log_handle = None

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    def load_cases(self) -> list[dict[str, Any]]:
        data = json.loads(self.cases_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("cases", [])
        return data

    def _log(self, message: str = "") -> None:
        print(message, flush=True)
        if self._log_handle is not None:
            self._log_handle.write(message + "\n")
            self._log_handle.flush()

    def _ensure_fixture_seed(self) -> None:
        files = {
            "project_info.txt": "Project: WenDream\nOwner: Felix\nVersion: 1\n",
            "notes.txt": "Benchmark Notes\nThis setup is deterministic and local.\n",
            "metrics.json": '{"status": "ok", "pass": true, "version": 1}\n',
            "jobs.html": "<html><body><div class='job'>AI Engineer - Shanghai - 300k - AI</div><div class='job'>Data Analyst - Beijing - 180k - Data</div><div class='job'>Product Manager - Shanghai - 260k - Product</div><div class='job'>ML Engineer - Shanghai - 320k - AI</div><div class='job'>Frontend Engineer - Hangzhou - 220k - Web</div></body></html>",
            "catalog.html": "<html><body><div class='product' data-name='Alpha' data-price='99' data-rating='4.8'></div><div class='product' data-name='Beta' data-price='129' data-rating='4.9'></div><div class='product' data-name='Gamma' data-price='89' data-rating='4.6'></div></body></html>",
            "order.txt": "ORDER-WD-48291\n",
            "scores.csv": "name,score\nAlice,82\nBob,95\nCharlie,88\n",
            "config.json": '{"timeout": 10, "retry": 3, "mode": "safe"}\n',
            "alpha.txt": "BENCHMARK_TARGET\nProjectAlpha\n",
            "beta.txt": "ProjectAlpha\n",
            "gamma.md": "# BENCHMARK_TARGET\nProjectAlpha\n",
            "delta.py": "BENCHMARK_TARGET\nProjectAlpha\n",
            "search_terms.txt": "WENDREAM_MAGIC_2026\n",
            "intentional_failure.py": "import sys\nprint(\"intentional failure\")\nsys.exit(1)\n",
            "calc.py": "result = 2 + 2\nprint(result)\n",
            "inventory.md": "# Inventory\nProjectAlpha\nProjectAlpha\n",
            "jobs_detail.html": "<html><body><div class='job'><span class='title'>AI Engineer</span><span class='city'>Shanghai</span><span class='salary'>320k</span><span class='direction'>AI</span></div><div class='job'><span class='title'>Backend Engineer</span><span class='city'>Beijing</span><span class='salary'>200k</span><span class='direction'>Backend</span></div><div class='job'><span class='title'>AI Researcher</span><span class='city'>Shanghai</span><span class='salary'>350k</span><span class='direction'>AI</span></div></body></html>",
            "page_old.html": "<html><body><div class='job'>上海 AI Engineer 320k</div><div class='job'>北京 Data Engineer 180k</div><div class='job'>深圳 ML Engineer 280k</div></body></html>",
            "page_new.html": "<html><body><div class='job'>上海 AI Engineer 320k</div><div class='job'>上海 Backend Engineer 220k</div><div class='job'>深圳 ML Engineer 240k</div></body></html>",
            "data folder/sample file.txt": "sample content\n",
        }
        for rel_path, content in files.items():
            path = self.fixtures_dir / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def _reset_dir(path: Path) -> bool:
        if path.exists():
            for _ in range(5):
                try:
                    shutil.rmtree(path)
                    break
                except (PermissionError, OSError):
                    time.sleep(0.3)
            else:
                return False
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return True

    def _prepare_workspace(self, case: dict[str, Any]) -> tuple[Path, list[str]]:
        warnings: list[str] = []
        case_id = case["id"]
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        workspace = self.workspaces_dir / case_id
        if not self._reset_dir(workspace):
            workspace = self.workspaces_dir / f"{case_id}__{int(time.time() * 1000)}"
            workspace.mkdir(parents=True, exist_ok=True)
            warnings.append("workspace_reset_failed_used_unique_dir")

        fixture = case.get("fixture")
        if fixture:
            fixture_path = self.fixtures_dir / fixture
            if fixture_path.exists():
                if fixture_path.is_dir():
                    shutil.copytree(fixture_path, workspace, dirs_exist_ok=True)
                else:
                    (workspace / fixture_path.name).write_bytes(fixture_path.read_bytes())
            else:
                warnings.append(f"fixture_missing:{fixture}")

        for entry in case.get("files", []):
            rel_path = entry.get("path") if isinstance(entry, dict) else None
            if not rel_path:
                continue
            target = workspace / str(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(entry.get("content", "")), encoding="utf-8")

        rules = case.get("permission_rules") or []
        if rules:
            import yaml

            rules_dir = workspace / ".wendream"
            rules_dir.mkdir(parents=True, exist_ok=True)
            (rules_dir / "permissions.yaml").write_text(
                yaml.dump(rules, allow_unicode=True), encoding="utf-8"
            )
        return workspace, warnings

    def _artifactize(self, case_id: str, tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把超大工具输出外置到 artifacts/，避免 trace / latest.json 膨胀。"""
        out: list[dict[str, Any]] = []
        for index, event in enumerate(tool_events):
            result = event.get("result")
            if not isinstance(result, str):
                out.append(event)
                continue
            size = len(result.encode("utf-8"))
            if size <= ARTIFACT_THRESHOLD_BYTES:
                out.append(event)
                continue
            tool_id = event.get("tool_id") or f"call_{index}"
            safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tool_id))
            target_dir = self.artifacts_dir / case_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{safe_id}.txt"
            target.write_text(result, encoding="utf-8")
            replacement = dict(event)
            replacement["result"] = {
                "externalized": True,
                "artifact_path": str(target),
                "size_bytes": size,
                "sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
                "preview_head": result[:PREVIEW_CHARS],
                "preview_tail": result[-PREVIEW_CHARS:],
            }
            out.append(replacement)
        return out

    # ------------------------------------------------------------------
    # Vision 能力
    # ------------------------------------------------------------------

    def vision_capability(self) -> dict[str, Any]:
        if self._vision_probe is None:
            from wendream.config import load_config

            provider = load_config().providers[0]
            probe = WenDreamAdapter.detect_vision_capability(provider)
            probe["provider"] = provider.name
            probe["model"] = provider.model
            self._vision_probe = probe
        return self._vision_probe

    # ------------------------------------------------------------------
    # 单 case 执行
    # ------------------------------------------------------------------

    def _skipped_result(self, case, workspace, category, reason, started, extra=None):
        result = {
            "case_id": case["id"],
            "category": category,
            "status": "SKIPPED",
            "skip_reason": reason,
            "workspace": str(workspace),
            "answer": "",
            "passed_checks": 0,
            "failed_checks": [],
            "session_id": None,
            "error": reason,
            "infrastructure_error": False,
            "timeout_stage": "",
            "latency_seconds": round(time.perf_counter() - started, 3),
            "timing": {},
            "counts": {},
            "provider": None,
            "model": None,
            "attachments": [],
            "tool_events": [],
            "permission_decisions": [],
            "transcript": [],
        }
        if extra:
            result.update(extra)
        return result

    def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = case["id"]
        category = (case.get("category") or case_id.split("_")[0]).upper()
        started = time.perf_counter()

        try:
            from wendream.config import load_config

            _provider = load_config().providers[0]
            provider_name, model_name = _provider.name, _provider.model
        except Exception:
            provider_name, model_name = "unknown", "unknown"

        prep_started = time.perf_counter()
        workspace, prep_warnings = self._prepare_workspace(case)
        prepare_seconds = time.perf_counter() - prep_started

        # --- 1) 明确标记为 SKIPPED 的用例（无稳定自动化接口） ---
        if case.get("expect_skipped"):
            result = self._skipped_result(
                case, workspace, category, case.get("skip_reason", "skipped"), started,
                {
                    "detected_web_handler": case.get("detected_web_handler"),
                    "missing_interface": case.get("missing_interface"),
                    "provider": provider_name,
                    "model": model_name,
                },
            )
            self._write_trace(case, result, workspace, prepare_seconds)
            return result

        # --- 2) Vision 能力检测 ---
        attachment_defs = case.get("attachments") or []
        if category == "VISION":
            capability = self.vision_capability()
            if not capability.get("supported"):
                reason = "model_does_not_support_vision"
                result = self._skipped_result(
                    case, workspace, category, reason, started,
                    {
                        "provider": capability.get("provider"),
                        "model": capability.get("model"),
                        "vision_probe": capability,
                        "attachments": attachment_defs,
                    },
                )
                self._write_trace(case, result, workspace, prepare_seconds)
                return result

        # --- 3) Session 专项流程 ---
        session_evidence: dict[str, Any] = {}
        if case.get("requires_disk_resume") or case.get("requires_session_isolation"):
            from wendream.config import load_config

            provider = load_config().providers[0]
            init_started = time.perf_counter()
            try:
                if case.get("requires_disk_resume"):
                    session_evidence = session_flows.run_disk_resume(
                        workspace,
                        provider,
                        seed_prompt=case["seed_prompt"],
                        followup_prompt=case["prompt"],
                        expect_in_final=case["expect_in_final"],
                        emit=self._log,
                    )
                    output = session_evidence.get("final_response", "")
                else:
                    session_evidence = session_flows.run_session_isolation(
                        workspace,
                        provider,
                        seed_a=case["seed_a"],
                        seed_b=case["seed_b"],
                        question_a=case["question_a"],
                        question_b=case["question_b"],
                        expect_a=case["expect_a"],
                        expect_b=case["expect_b"],
                        forbid_in_a=case.get("forbid_in_a", []),
                        forbid_in_b=case.get("forbid_in_b", []),
                        emit=self._log,
                    )
                    output = f"{session_evidence.get('answer_a','')}\n{session_evidence.get('answer_b','')}"
                agent_init_seconds = 0.0
                latency = time.perf_counter() - started - prepare_seconds
                session_timed_out = session_evidence.get("status") == "TIMEOUT"
                response = {
                    "status": "TIMEOUT" if session_timed_out else "OK",
                    "tool_events": session_evidence.get("tool_calls", []),
                    "permission_decisions": session_evidence.get("permission_decisions", []),
                    "transcript": session_evidence.get("transcript", []),
                    "metrics": {
                        "agent_turn_count": session_evidence.get("turn_index", 0),
                        "llm_call_count": session_evidence.get("llm_call_count", 0),
                        "tool_call_count": session_evidence.get("tool_call_count", 0),
                        "permission_request_count": session_evidence.get("permission_request_count", 0),
                        "permission_denied_count": session_evidence.get("permission_denied_count", 0),
                        "tool_seconds": 0.0,
                        "session_seconds": 0.0,
                    },
                    "session_id": session_evidence.get("session_id")
                    or session_evidence.get("session_a_id"),
                    "timeout": session_timed_out,
                    "timeout_stage": session_evidence.get("timeout_stage", ""),
                    "errors": [session_evidence.get("error")] if session_evidence.get("error") else [],
                    "infrastructure_error": False,
                    "attachments": [],
                }
            except Exception as exc:  # pragma: no cover - 运行期异常
                response = {
                    "status": "INFRASTRUCTURE_ERROR",
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "tool_events": [],
                    "permission_decisions": [],
                    "transcript": [],
                    "metrics": {},
                    "session_id": None,
                    "timeout": False,
                    "infrastructure_error": True,
                    "attachments": [],
                }
                output = ""
                session_evidence = {"status": "INFRASTRUCTURE_ERROR", "error": f"{type(exc).__name__}: {exc}"}
                agent_init_seconds = 0.0
                latency = time.perf_counter() - started - prepare_seconds
        else:
            # --- 4) 常规 agent 执行 ---
            permission_paths = [str(workspace / e["path"]) for e in case.get("files", []) if e.get("path")]
            file_state_before = {}
            for p in permission_paths:
                fp = Path(p)
                file_state_before[fp.name] = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else None

            init_started = time.perf_counter()
            case_deadline = (
                init_started + self.case_timeout_seconds if self.case_timeout_seconds else None
            )
            base_turn_timeout = float(case.get("timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS))
            budget_exhausted = False
            last_progress = {"time": 0.0, "stage": ""}

            def _remaining() -> float | None:
                if case_deadline is None:
                    return None
                return max(case_deadline - time.perf_counter(), 0.0)

            def on_progress(info: dict[str, Any]) -> None:
                if not self.show_progress:
                    return
                now = time.perf_counter()
                stage = info.get("stage", "")
                if stage == last_progress["stage"] and now - last_progress["time"] < 5:
                    return
                last_progress["time"] = now
                last_progress["stage"] = stage
                metrics = info.get("metrics") or {}
                self._log(
                    f"      \u00b7 stage={stage} elapsed={now - init_started:.1f}s "
                    f"llm={metrics.get('llm_call_count', 0)} "
                    f"tool={metrics.get('tool_call_count', 0)} "
                    f"perm={metrics.get('permission_request_count', 0)}"
                )

            adapter = WenDreamAdapter(
                work_dir=str(workspace),
                enable_fallback=False,
                permission_policy=case.get("permission_policy", "allow"),
                timeout_seconds=base_turn_timeout,
                on_progress=on_progress,
            )
            agent_init_seconds = time.perf_counter() - init_started

            run_started = time.perf_counter()

            def _turn_timeout() -> float | None:
                remaining = _remaining()
                if remaining is None:
                    return base_turn_timeout
                return min(base_turn_timeout, remaining)

            try:
                history = case.get("history") or case.get("conversation") or []
                attachments = [
                    {**a, "path": str(self.fixtures_dir / a["path"])}
                    for a in attachment_defs
                    if a.get("path")
                ] or None
                for turn in history:
                    prompt = turn.get("prompt") if isinstance(turn, dict) else str(turn)
                    if not prompt:
                        continue
                    turn_budget = _turn_timeout()
                    if turn_budget is not None and turn_budget <= 0:
                        budget_exhausted = True
                        break
                    adapter.run_prompt_sync(
                        prompt, workspace=str(workspace), timeout_seconds=turn_budget
                    )
                if not budget_exhausted:
                    turn_budget = _turn_timeout()
                    if turn_budget is not None and turn_budget <= 0:
                        budget_exhausted = True
                        response = {
                            "status": "TIMEOUT",
                            "timeout": True,
                            "errors": [
                                f"case budget {self.case_timeout_seconds}s exhausted before final turn"
                            ],
                            "tool_events": list(adapter.tool_events),
                            "permission_decisions": list(adapter.permission_decisions),
                            "transcript": list(adapter.transcript),
                            "metrics": dict(adapter.metrics),
                            "session_id": adapter.session.session_id,
                            "infrastructure_error": False,
                            "attachments": list(adapter.attachments),
                        }
                    else:
                        response = adapter.run_prompt_sync(
                            case.get("prompt") or "",
                            workspace=str(workspace),
                            attachments=attachments,
                            timeout_seconds=turn_budget,
                        )
                else:
                    response = {
                        "status": "TIMEOUT",
                        "timeout": True,
                        "errors": [
                            f"case budget {self.case_timeout_seconds}s exhausted during history turns"
                        ],
                        "tool_events": list(adapter.tool_events),
                        "permission_decisions": list(adapter.permission_decisions),
                        "transcript": list(adapter.transcript),
                        "metrics": dict(adapter.metrics),
                        "session_id": adapter.session.session_id,
                        "infrastructure_error": False,
                        "attachments": list(adapter.attachments),
                    }
                output = response.get("final_response") or ""
            finally:
                try:
                    adapter.close()
                except Exception:
                    pass
            latency = time.perf_counter() - run_started

            file_state_after = {}
            for p in permission_paths:
                fp = Path(p)
                file_state_after[fp.name] = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else None
            session_evidence["file_state_before"] = file_state_before
            session_evidence["file_state_after"] = file_state_after

        # --- 5) 评测 ---
        checks = case.get("checks", [])
        tool_events = response.get("tool_events", []) or []
        context = {
            "transcript": "\n".join(
                entry.get("content", "") for entry in (response.get("transcript") or [])
            ),
            "tool_events": tool_events,
            "permission_decisions": response.get("permission_decisions", []) or [],
            "session": session_evidence,
        }

        eval_started = time.perf_counter()
        passed_checks: list[dict[str, Any]] = []
        failed_checks: list[dict[str, Any]] = []
        evaluator_error = ""
        for check in checks:
            try:
                ok = evaluate_check(output, check, workspace=str(workspace), context=context)
            except Exception as exc:
                evaluator_error = f"{type(exc).__name__}: {exc}"
                failed_checks.append({"check": check, "error": evaluator_error})
                continue
            if ok:
                passed_checks.append(check)
            else:
                failed_checks.append({"check": check, "actual": output[:400]})
        evaluation_seconds = time.perf_counter() - eval_started

        # --- 6) 状态判定 ---
        total_elapsed = time.perf_counter() - started
        case_budget_exceeded = bool(self.case_timeout_seconds) and total_elapsed > self.case_timeout_seconds
        timed_out = bool(response.get("timeout")) or case_budget_exceeded
        infrastructure_error = bool(response.get("infrastructure_error"))
        attachments_missing = any(a.get("missing") for a in (response.get("attachments") or []))

        if response.get("document_unsupported"):
            status = "SKIPPED"
            failure_reason = response.get("skip_reason")
        elif not checks:
            status = "BENCHMARK_DESIGN_ERROR"
            failure_reason = "case declares no checks, so it cannot measure anything"
        elif evaluator_error:
            status = "EVALUATOR_ERROR"
            failure_reason = evaluator_error
        elif attachments_missing:
            status = "BENCHMARK_DESIGN_ERROR"
            failure_reason = "declared attachments are missing on disk"
        elif timed_out:
            status = "TIMEOUT"
            if case_budget_exceeded and not response.get("timeout"):
                failure_reason = (
                    f"case budget exceeded: {total_elapsed:.1f}s > {self.case_timeout_seconds}s"
                )
            else:
                failure_reason = (response.get("errors") or ["turn timeout"])[0]
        elif infrastructure_error:
            status = "INFRASTRUCTURE_ERROR"
            failure_reason = (response.get("errors") or ["infrastructure failure"])[0]
        elif failed_checks:
            status = "FAILED"
            failure_reason = "checks failed: " + ", ".join(
                str(c.get("check", {}).get("type")) for c in failed_checks
            )
        else:
            status = "PASSED"
            failure_reason = None

        metrics = response.get("metrics") or {}
        tool_events = self._artifactize(case_id, tool_events)

        result = {
            "case_id": case_id,
            "category": category,
            "status": status,
            "workspace": str(workspace),
            "answer": output,
            "passed_checks": len(passed_checks),
            "failed_checks": failed_checks,
            "session_id": response.get("session_id"),
            "error": failure_reason,
            "infrastructure_error": infrastructure_error,
            "timeout_stage": response.get("timeout_stage", ""),
            "latency_seconds": round(latency, 3),
            "provider": provider_name,
            "model": model_name,
            "attachments": response.get("attachments") or [],
            "tool_events": tool_events,
            "permission_decisions": response.get("permission_decisions") or [],
            "transcript": response.get("transcript") or [],
            "timing": {
                "prepare_workspace_seconds": round(prepare_seconds, 3),
                "agent_init_seconds": round(agent_init_seconds, 3),
                "agent_run_seconds": round(latency, 3),
                "tool_seconds": round(float(metrics.get("tool_seconds", 0.0) or 0.0), 3),
                "session_seconds": round(float(metrics.get("session_seconds", 0.0) or 0.0), 3),
                "evaluation_seconds": round(evaluation_seconds, 3),
                "total_seconds": round(time.perf_counter() - started, 3),
            },
            "counts": {
                "agent_turn_count": int(metrics.get("agent_turn_count", 0) or 0),
                "llm_call_count": int(metrics.get("llm_call_count", 0) or 0),
                "tool_call_count": int(metrics.get("tool_call_count", 0) or 0),
                "permission_request_count": int(metrics.get("permission_request_count", 0) or 0),
                "permission_denied_count": int(metrics.get("permission_denied_count", 0) or 0),
            },
            "session_evidence": session_evidence,
            "prep_warnings": prep_warnings,
            "checks": checks,
            "skip_reason": response.get("skip_reason"),
            "document_unsupported": bool(response.get("document_unsupported")),
        }
        self._write_trace(case, result, workspace, prepare_seconds)
        return result

    # ------------------------------------------------------------------
    # Trace
    # ------------------------------------------------------------------

    def _write_trace(self, case, result, workspace, prepare_seconds) -> None:
        case_id = case["id"]
        trace = {
            "case_id": case_id,
            "category": result["category"],
            "status": result["status"],
            "prompt": case.get("prompt"),
            "turns": case.get("history") or case.get("conversation") or [],
            "workspace": str(workspace),
            "provider": result.get("provider") or (self._vision_probe or {}).get("provider"),
            "model": result.get("model") or (self._vision_probe or {}).get("model"),
            "attachments": result.get("attachments") or case.get("attachments") or [],
            "session_id": result.get("session_id"),
            "agent_instance_ids": [],
            "agent_messages": result.get("transcript") or [],
            "tool_calls": result.get("tool_events") or [],
            "permission_decisions": result.get("permission_decisions") or [],
            "permission_rules": case.get("permission_rules") or [],
            "session_evidence": result.get("session_evidence") or {},
            "final_response": result.get("answer"),
            "timing": result.get("timing"),
            "counts": result.get("counts"),
            "evaluation": {
                "passed_checks": result.get("passed_checks", 0),
                "failed_checks": result.get("failed_checks", []),
                "checks": result.get("checks", []),
            },
            "failure_reason": result.get("error"),
            "bad_case_type": "",
            "skip_reason": result.get("skip_reason"),
            "prep_warnings": result.get("prep_warnings") or [],
        }
        if result["status"] == "SKIPPED":
            trace["detected_web_handler"] = case.get("detected_web_handler")
            trace["missing_interface"] = case.get("missing_interface")
            trace["vision_probe"] = result.get("vision_probe")
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        (self.traces_dir / f"{case_id}.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    @staticmethod
    def _stats(values: list[float]) -> dict[str, float]:
        if not values:
            return {"avg": 0.0, "median": 0.0}
        return {
            "avg": round(sum(values) / len(values), 2),
            "median": round(statistics.median(values), 2),
        }

    def run(self, case_ids: list[str] | None = None) -> list[dict[str, Any]]:
        self._ensure_fixture_seed()
        cases = self.load_cases()
        if case_ids:
            by_id = {c["id"]: c for c in cases}
            cases = [by_id[cid] for cid in case_ids if cid in by_id]

        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.results_dir / "run_stdout.log"
        self._log_handle = log_path.open("w", encoding="utf-8")

        started = time.perf_counter()
        try:
            from wendream.config import load_config

            provider = load_config().providers[0]
            provider_name, model_name = provider.name, provider.model
        except Exception:
            provider_name, model_name = "unknown", "unknown"

        self._log("WenDream Benchmark v1_validated")
        self._log(f"Provider: {provider_name}")
        self._log(f"Model: {model_name}")
        self._log(f"Total Cases: {len(cases)}")
        self._log("-" * 62)

        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            label = f"[{index:02d}/{len(cases):02d}]"
            self._log(f"{label} START {case['id']}")
            result = self.run_case(case)
            results.append(result)

            elapsed = float(result.get("latency_seconds", 0.0) or 0.0)
            status = result["status"]
            if status == "PASSED":
                self._log(f"{label} PASS {case['id']} | {elapsed:.2f}s")
            elif status == "FAILED":
                self._log(f"{label} FAIL {case['id']} | {elapsed:.2f}s | reason={result.get('error')}")
            elif status == "SKIPPED":
                self._log(f"{label} SKIP {case['id']} | reason={result.get('skip_reason')}")
            elif status == "TIMEOUT":
                self._log(f"{label} TIMEOUT {case['id']} | {elapsed:.2f}s | stage={result.get('timeout_stage')}")
            else:
                self._log(f"{label} {status} {case['id']} | reason={result.get('error')}")

            counts = result.get("counts") or {}
            if counts.get("llm_call_count", 0) > 5:
                self._log(f"WARNING: EXCESSIVE_AGENT_LOOP {case['id']} llm_call_count={counts['llm_call_count']}")
            stage = result.get("timeout_stage") or "-"
            if elapsed > 60:
                self._log(f"WARNING: VERY_SLOW CASE {case['id']} elapsed={elapsed:.2f}s stage={stage}")
            elif elapsed > 30:
                self._log(f"WARNING: SLOW CASE {case['id']} elapsed={elapsed:.2f}s stage={stage}")
        total_seconds = time.perf_counter() - started

        self._write_reports(results, total_seconds)
        return results

    def _write_reports(self, results: list[dict[str, Any]], total_seconds: float) -> None:
        counts = {status: 0 for status in VALID_STATUSES}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        denom = counts["PASSED"] + counts["FAILED"] + counts["TIMEOUT"]
        success_rate = task_success_rate(counts["PASSED"], counts["FAILED"], counts["TIMEOUT"])

        latencies = [float(r.get("latency_seconds") or 0.0) for r in results if r["status"] != "SKIPPED"]
        llm_calls = [float((r.get("counts") or {}).get("llm_call_count", 0)) for r in results if r["status"] != "SKIPPED"]
        tool_calls = [float((r.get("counts") or {}).get("tool_call_count", 0)) for r in results if r["status"] != "SKIPPED"]

        summary = []
        for r in results:
            summary.append({
                "case_id": r["case_id"],
                "category": r["category"],
                "status": r["status"],
                "score": 1.0 if r["status"] == "PASSED" else 0.0,
                "latency_seconds": r.get("latency_seconds"),
                "llm_call_count": (r.get("counts") or {}).get("llm_call_count", 0),
                "tool_call_count": (r.get("counts") or {}).get("tool_call_count", 0),
                "permission_request_count": (r.get("counts") or {}).get("permission_request_count", 0),
                "session_id": r.get("session_id"),
                "provider": r.get("provider"),
                "model": r.get("model"),
                "failure_reason": r.get("error"),
                "bad_case_type": r["status"] if r["status"] != "PASSED" else "",
                "trace_path": str(self.traces_dir / f"{r['case_id']}.json"),
                "artifact_paths": [
                    e["result"]["artifact_path"]
                    for e in (r.get("tool_events") or [])
                    if isinstance(e.get("result"), dict) and e["result"].get("artifact_path")
                ],
            })

        (self.results_dir / "latest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        with (self.results_dir / "latest.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()) if summary else ["case_id"])
            writer.writeheader()
            for row in summary:
                writer.writerow(row)

        category_stats: dict[str, dict[str, Any]] = {}
        for r in results:
            key = r["category"].lower()
            entry = category_stats.setdefault(
                key, {"count": 0, "passed": 0, "failed": [], "skipped": [], "timeout": [],
                      "infra": [], "evaluator_error": [], "design_error": [], "wall": 0.0}
            )
            entry["count"] += 1
            entry["wall"] += float((r.get("timing") or {}).get("total_seconds", 0.0) or 0.0)
            st = r["status"]
            if st == "PASSED":
                entry["passed"] += 1
            elif st == "FAILED":
                entry["failed"].append(r["case_id"])
            elif st == "SKIPPED":
                entry["skipped"].append(r["case_id"])
            elif st == "TIMEOUT":
                entry["timeout"].append(r["case_id"])
            elif st == "INFRASTRUCTURE_ERROR":
                entry["infra"].append(r["case_id"])
            elif st == "EVALUATOR_ERROR":
                entry["evaluator_error"].append(r["case_id"])
            elif st == "BENCHMARK_DESIGN_ERROR":
                entry["design_error"].append(r["case_id"])

        (self.results_dir / "timing.json").write_text(
            json.dumps(category_stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        slowest = sorted(results, key=lambda r: float(r.get("latency_seconds") or 0), reverse=True)[:5]
        most_llm = sorted(results, key=lambda r: (r.get("counts") or {}).get("llm_call_count", 0), reverse=True)[:5]
        most_tool = sorted(results, key=lambda r: (r.get("counts") or {}).get("tool_call_count", 0), reverse=True)[:5]

        md: list[str] = [
            "# Benchmark Report — v1_validated",
            "",
            f"- Total cases: {len(results)}",
            f"- PASSED: {counts['PASSED']}",
            f"- FAILED: {counts['FAILED']}",
            f"- TIMEOUT: {counts['TIMEOUT']}",
            f"- SKIPPED: {counts['SKIPPED']}",
            f"- INFRASTRUCTURE_ERROR: {counts['INFRASTRUCTURE_ERROR']}",
            f"- EVALUATOR_ERROR: {counts['EVALUATOR_ERROR']}",
            f"- BENCHMARK_DESIGN_ERROR: {counts['BENCHMARK_DESIGN_ERROR']}",
            "",
            "**Task Success Rate = PASSED / (PASSED + FAILED + TIMEOUT) = "
            f"{counts['PASSED']} / {denom} = {success_rate:.1f}%**",
            "",
            f"- Total wall time: {total_seconds:.2f}s",
            f"- Average latency: {self._stats(latencies)['avg']:.2f}s | Median: {self._stats(latencies)['median']:.2f}s",
            f"- Average LLM calls: {self._stats(llm_calls)['avg']:.2f} | Median: {self._stats(llm_calls)['median']:.2f}",
            f"- Average Tool calls: {self._stats(tool_calls)['avg']:.2f} | Median: {self._stats(tool_calls)['median']:.2f}",
            "",
            "## Summary by category",
        ]
        for name, entry in category_stats.items():
            md.append(
                f"- {name}: {entry['passed']}/{entry['count']} passed | wall={entry['wall']:.2f}s"
            )
            for key, label in (
                ("failed", "failed"),
                ("timeout", "timeout"),
                ("skipped", "skipped"),
                ("infra", "infrastructure_error"),
                ("evaluator_error", "evaluator_error"),
                ("design_error", "benchmark_design_error"),
            ):
                if entry[key]:
                    md.append(f"    - {label}: {', '.join(entry[key])}")

        md += ["", "## Slowest 5 cases"]
        for r in slowest:
            md.append(f"- {r['case_id']}: {float(r.get('latency_seconds') or 0):.2f}s ({r['status']})")
        md += ["", "## Most LLM calls (top 5)"]
        for r in most_llm:
            md.append(f"- {r['case_id']}: {(r.get('counts') or {}).get('llm_call_count', 0)}")
        md += ["", "## Most Tool calls (top 5)"]
        for r in most_tool:
            md.append(f"- {r['case_id']}: {(r.get('counts') or {}).get('tool_call_count', 0)}")

        md += ["", "## Non-passing cases"]
        for r in results:
            if r["status"] != "PASSED":
                md.append(f"- {r['case_id']} | {r['status']} | {r.get('error') or r.get('skip_reason')}")

        (self.results_dir / "report.md").write_text("\n".join(md), encoding="utf-8")

        bad = [r for r in results if r["status"] != "PASSED"]
        (self.results_dir / "bad_cases.md").write_text(
            "\n".join(
                f"- {r['case_id']} | {r['status']} | {r.get('error') or r.get('skip_reason') or ''}"
                for r in bad
            ),
            encoding="utf-8",
        )

        self._log("-" * 62)
        self._log("Category summary")
        for name, entry in category_stats.items():
            self._log(f"  {name}: {entry['passed']}/{entry['count']} passed | wall={entry['wall']:.2f}s")
        self._log("-" * 62)
        self._log(
            f"DONE total={len(results)} passed={counts['PASSED']} failed={counts['FAILED']} "
            f"timeout={counts['TIMEOUT']} skipped={counts['SKIPPED']} "
            f"success_rate={success_rate:.1f}%"
        )
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run WenDream Benchmark v1_validated")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--cases-path", default=None)
    parser.add_argument(
        "--results-dir",
        default=None,
        help="结果子目录名（相对 benchmark/results/）。默认 VERSION；"
        "回归验证请用 post_audit_regression，避免覆盖 final_release。",
    )
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=None,
        help="每个 Case 的总预算（秒）。设置后每轮 timeout 会被剩余预算截断；"
        "执行完成若已超预算则判为 TIMEOUT。默认 None（不启用）。",
    )
    args = parser.parse_args()
    kwargs: dict = {}
    if args.results_dir:
        kwargs["results_dir_name"] = args.results_dir
    runner = ValidatedBenchmarkRunner(
        cases_path=args.cases_path,
        case_timeout_seconds=args.case_timeout,
        **kwargs,
    )
    runner.run(args.case_id or None)


if __name__ == "__main__":
    main()
