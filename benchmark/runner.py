from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from pathlib import Path
from typing import Any

from benchmark.adapters.wendream_adapter import DEFAULT_TURN_TIMEOUT_SECONDS, WenDreamAdapter
from benchmark.evaluator import evaluate_check

VALID_STATUSES = {"PASSED", "FAILED", "SKIPPED", "TIMEOUT", "INFRASTRUCTURE_ERROR"}


class BenchmarkRunner:
    def __init__(self, base_dir: str | Path | None = None, cases_path: str | Path | None = None):
        raw_base = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent
        candidates = [raw_base]
        if raw_base.name != "benchmark":
            candidates.append(raw_base / "benchmark")
        self.base_dir = next(
            (p for p in candidates if (p / "cases").exists() or (p / "fixtures").exists() or p.name == "benchmark"),
            raw_base,
        )
        default_cases_path = self.base_dir / "cases" / "benchmark_v1.json"
        self.cases_path = Path(cases_path) if cases_path is not None else default_cases_path
        self.results_dir = self.base_dir / "results" / "v1"
        self.traces_dir = self.results_dir / "traces"

    def load_cases(self) -> list[dict[str, Any]]:
        if not self.cases_path.exists():
            return []
        data = json.loads(self.cases_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("cases", [])
        return data

    def _ensure_fixture_seed(self) -> None:
        fixture_dir = self.base_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
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
            path = fixture_dir / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def _reset_dir(path: Path) -> None:
        """清空并重建工作目录。

        Windows 下会话文件可能被其它进程（例如上一轮卡死的 run）短暂占用，
        直接 rmtree 会抛 PermissionError 并让整个 benchmark 崩掉。这里做
        有限重试，仍然失败则由调用方退化为唯一目录。
        """
        if path.exists():
            last_error: Exception | None = None
            for _ in range(5):
                try:
                    shutil.rmtree(path)
                    last_error = None
                    break
                except (PermissionError, OSError) as exc:  # pragma: no cover - 平台相关
                    last_error = exc
                    time.sleep(0.3)
            if last_error is not None:
                raise last_error
        path.mkdir(parents=True, exist_ok=True)

    def _prepare_workspace(self, case: dict[str, Any]) -> Path:
        case_id = case["id"]
        workspace_root = self.base_dir / "workspaces"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = workspace_root / case_id
        try:
            self._reset_dir(workspace)
        except (PermissionError, OSError):
            # 目录被外部进程占用时，退化为带时间戳的唯一目录，
            # 保证 benchmark 仍然可以执行并如实产出结果。
            workspace = workspace_root / f"{case_id}__{int(time.time() * 1000)}"
            workspace.mkdir(parents=True, exist_ok=True)

        fixture = case.get("fixture")
        if fixture:
            fixture_path = self.base_dir / "fixtures" / fixture
            if fixture_path.exists():
                if fixture_path.is_dir():
                    shutil.copytree(fixture_path, workspace, dirs_exist_ok=True)
                else:
                    target = workspace / fixture_path.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fixture_path, target)

        for entry in case.get("files", []):
            if not isinstance(entry, dict):
                continue
            rel_path = entry.get("path") or entry.get("name")
            if rel_path is None:
                continue
            target = workspace / str(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(entry.get("content", "")), encoding="utf-8")
        return workspace

    def _supports_vision(self) -> bool:
        return False

    def _supports_session_resume(self) -> bool:
        return True

    def _evaluate_checks(self, output: str, checks: list[dict[str, Any]], workspace: Path):
        passed = []
        failed = []
        for check in checks:
            try:
                ok = evaluate_check(output, check, workspace=str(workspace))
                if ok:
                    passed.append(check)
                else:
                    failed.append({"check": check, "actual": output})
            except Exception as exc:  # pragma: no cover - validation guard
                failed.append({"check": check, "error": str(exc)})
        return passed, failed

    def _run_turns(self, case: dict[str, Any], adapter: WenDreamAdapter, workspace: Path):
        history = case.get("history") or case.get("conversation") or []
        if not history:
            return adapter.run_prompt_sync(case.get("prompt") or "", workspace=str(workspace))
        for turn in history:
            prompt = turn.get("prompt") if isinstance(turn, dict) else str(turn)
            if prompt:
                adapter.run_prompt_sync(prompt, workspace=str(workspace))
        return adapter.run_prompt_sync(case.get("prompt") or "", workspace=str(workspace))

    def run_case_definition(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = case.get("id", "LOCAL")
        category = (case.get("category") or case_id.split("_")[0]).upper()
        case_started = time.perf_counter()
        prepare_started = time.perf_counter()
        workspace = self._prepare_workspace(case)
        prepare_seconds = time.perf_counter() - prepare_started

        if category == "VISION" and not self._supports_vision():
            return {
                "case_id": case_id,
                "category": category,
                "status": "SKIPPED",
                "workspace": str(workspace),
                "answer": "",
                "passed_checks": 0,
                "failed_checks": [],
                "session_id": None,
                "error": "Vision case has no image/PDF input configured in the case catalog, and the adapter has no multimodal attachment path.",
                "infrastructure_error": False,
                "skip_reason": "no_image_input_configured",
                "latency_seconds": round(time.perf_counter() - case_started, 3),
                "timing": {
                    "prepare_workspace_seconds": round(prepare_seconds, 3),
                    "agent_init_seconds": 0.0,
                    "agent_run_seconds": 0.0,
                    "llm_seconds": 0.0,
                    "tool_seconds": 0.0,
                    "session_seconds": 0.0,
                    "evaluation_seconds": 0.0,
                    "total_seconds": round(time.perf_counter() - case_started, 3),
                },
                "counts": {
                    "agent_turn_count": 0,
                    "llm_call_count": 0,
                    "tool_call_count": 0,
                    "permission_request_count": 0,
                },
                "timeout_stage": "",
                "provider": None,
                "model": None,
            }

        if category == "SESSION" and not self._supports_session_resume():
            return {
                "case_id": case_id,
                "category": category,
                "status": "SKIPPED",
                "workspace": str(workspace),
                "answer": "",
                "passed_checks": 0,
                "failed_checks": [],
                "session_id": None,
                "error": "Session resume is not available in the current runtime.",
                "infrastructure_error": False,
                "skip_reason": "session_resume_unavailable",
                "latency_seconds": round(time.perf_counter() - case_started, 3),
                "timing": {
                    "prepare_workspace_seconds": round(prepare_seconds, 3),
                    "agent_init_seconds": 0.0,
                    "agent_run_seconds": 0.0,
                    "llm_seconds": 0.0,
                    "tool_seconds": 0.0,
                    "session_seconds": 0.0,
                    "evaluation_seconds": 0.0,
                    "total_seconds": round(time.perf_counter() - case_started, 3),
                },
                "counts": {
                    "agent_turn_count": 0,
                    "llm_call_count": 0,
                    "tool_call_count": 0,
                    "permission_request_count": 0,
                },
                "timeout_stage": "",
                "provider": None,
                "model": None,
            }

        # 权限策略按 Case 显式指定，默认与 CLI `-p` 非交互模式一致使用 allow，
        # 避免默认 deny 意外拒绝普通 fixture 读写。需要验证"拒绝路径"的用例
        # （如 ERROR_002）在 case 数据里显式声明 permission_policy: deny。
        init_started = time.perf_counter()
        adapter = WenDreamAdapter(
            work_dir=str(workspace),
            enable_fallback=False,
            permission_policy=case.get("permission_policy", "allow"),
            timeout_seconds=case.get("timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS),
        )
        agent_init_seconds = time.perf_counter() - init_started

        start = time.perf_counter()
        try:
            response = self._run_turns(case, adapter, workspace)
        finally:
            # 释放 session 文件句柄，避免 Windows 下 workspace 被锁住。
            try:
                adapter.close()
            except Exception:  # pragma: no cover - 资源回收尽力而为
                pass
        output = response.get("final_response") or response.get("answer") or ""
        latency = time.perf_counter() - start

        adapter_metrics = response.get("metrics") or getattr(adapter, "metrics", {}) or {}
        tool_seconds = float(adapter_metrics.get("tool_seconds", 0.0) or 0.0)
        session_seconds = float(adapter_metrics.get("session_seconds", 0.0) or 0.0)
        # 无法精确拆分 LLM 与 Agent 编排时间，用"总时长 - 工具 - 会话"作为 LLM 侧耗时。
        llm_seconds = max(latency - tool_seconds - session_seconds, 0.0)

        checks = case.get("checks", [])
        eval_started = time.perf_counter()
        passed, failed = self._evaluate_checks(output, checks, workspace)
        evaluation_seconds = time.perf_counter() - eval_started
        infrastructure_error = bool(response.get("infrastructure_error"))
        timed_out = bool(response.get("timeout"))

        if timed_out:
            status = "TIMEOUT"
            failure_reason = (response.get("errors") or ["Agent turn timed out."])[0]
        elif infrastructure_error:
            status = "INFRASTRUCTURE_ERROR"
            failure_reason = response.get("errors", ["Provider or infrastructure failure."])[0]
        elif checks and not failed:
            status = "PASSED"
            failure_reason = None
        elif checks and failed:
            status = "FAILED"
            failure_reason = failed[0].get("error") or "Evaluator reported a mismatch."
        else:
            status = "FAILED"
            failure_reason = "No evaluation checks were available."

        trace = {
            "case_id": case_id,
            "category": category,
            "prompt": case.get("prompt"),
            "turns": case.get("history") or case.get("conversation") or [],
            "workspace": str(workspace),
            "provider": getattr(getattr(adapter, "provider", None), "name", None),
            "model": getattr(getattr(adapter, "provider", None), "model", None),
            "session_id": response.get("session_id"),
            "agent_instance_ids": [id(adapter)],
            "input_attachments": [],
            "agent_messages": [{"role": "user", "content": case.get("prompt")}, {"role": "assistant", "content": output}],
            "tool_calls": response.get("tool_events", []),
            "tool_arguments": [event.get("arguments") for event in response.get("tool_events", [])],
            "tool_results": [event.get("result") for event in response.get("tool_events", [])],
            "tool_errors": [event for event in response.get("tool_events", []) if event.get("is_error")],
            "permission_decisions": response.get("permission_decisions", []),
            "final_response": output,
            "latency_seconds": round(latency, 3),
            "evaluation": {"passed_checks": len(passed), "failed_checks": len(failed), "checks": checks},
            "status": status,
            "failure_reason": failure_reason,
            "bad_case_type": "" if status == "PASSED" else "real_execution",
            "timeout_stage": response.get("timeout_stage", ""),
            "timing": {
                "prepare_workspace_seconds": round(prepare_seconds, 3),
                "agent_init_seconds": round(agent_init_seconds, 3),
                "agent_run_seconds": round(latency, 3),
                "llm_seconds": round(llm_seconds, 3),
                "tool_seconds": round(tool_seconds, 3),
                "session_seconds": round(session_seconds, 3),
                "evaluation_seconds": round(evaluation_seconds, 3),
                "total_seconds": round(time.perf_counter() - case_started, 3),
            },
            "counts": {
                "agent_turn_count": int(adapter_metrics.get("agent_turn_count", 0) or 0),
                "llm_call_count": int(adapter_metrics.get("llm_call_count", 0) or 0),
                "tool_call_count": int(adapter_metrics.get("tool_call_count", 0) or 0),
                "permission_request_count": int(adapter_metrics.get("permission_request_count", 0) or 0),
            },
        }
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        (self.traces_dir / f"{case_id}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

        report = {
            "case_id": case_id,
            "category": category,
            "status": status,
            "workspace": str(workspace),
            "answer": output,
            "passed_checks": len(passed),
            "failed_checks": failed,
            "session_id": response.get("session_id"),
            "error": failure_reason,
            "infrastructure_error": infrastructure_error,
            "tool_events": response.get("tool_events", []),
            "latency_seconds": round(latency, 3),
            "trace_path": str(self.traces_dir / f"{case_id}.json"),
            "timing": {
                "prepare_workspace_seconds": round(prepare_seconds, 3),
                "agent_init_seconds": round(agent_init_seconds, 3),
                "agent_run_seconds": round(latency, 3),
                "llm_seconds": round(llm_seconds, 3),
                "tool_seconds": round(tool_seconds, 3),
                "session_seconds": round(session_seconds, 3),
                "evaluation_seconds": round(evaluation_seconds, 3),
                "total_seconds": round(time.perf_counter() - case_started, 3),
            },
            "counts": {
                "agent_turn_count": int(adapter_metrics.get("agent_turn_count", 0) or 0),
                "llm_call_count": int(adapter_metrics.get("llm_call_count", 0) or 0),
                "tool_call_count": int(adapter_metrics.get("tool_call_count", 0) or 0),
                "permission_request_count": int(adapter_metrics.get("permission_request_count", 0) or 0),
            },
            "timeout_stage": response.get("timeout_stage", ""),
            "provider": getattr(getattr(adapter, "provider", None), "name", None),
            "model": getattr(getattr(adapter, "provider", None), "model", None),
        }
        return report

    CATEGORY_ALIASES = {
        "context": "context",
        "file": "file operations",
        "file_operation": "file operations",
        "file_operations": "file operations",
        "tool": "tool calling",
        "tool_calling": "tool calling",
        "information": "information processing",
        "info": "information processing",
        "information_processing": "information processing",
        "vision": "vision",
        "multimodal": "vision",
        "session": "session",
        "error_recovery": "error recovery",
        "error": "error recovery",
    }

    @classmethod
    def _category_key(cls, case: dict[str, Any]) -> str:
        raw = (case.get("category") or case.get("id", "").split("_")[0]).strip().lower()
        return cls.CATEGORY_ALIASES.get(raw, raw)

    @classmethod
    def _normalize_category(cls, category: str) -> str:
        key = category.strip().lower()
        if key not in cls.CATEGORY_ALIASES:
            raise ValueError(
                f"Unknown category '{category}'. Valid: {', '.join(sorted(set(cls.CATEGORY_ALIASES)))}"
            )
        return cls.CATEGORY_ALIASES[key]

    def _describe_provider(self, cases: list[dict[str, Any]]) -> tuple[str, str]:
        # 只读配置，不构造 adapter，避免在工作区产生多余的 session 目录。
        try:
            from wendream.config import load_config

            config = load_config()
            if config.providers:
                provider = config.providers[0]
                return str(provider.name), str(provider.model)
        except Exception:
            pass
        return "unknown", "unknown"

    @staticmethod
    def _dominant_stage(result: dict[str, Any]) -> str:
        timing = result.get("timing") or {}
        stages = {
            "llm": float(timing.get("llm_seconds", 0.0) or 0.0),
            "tool": float(timing.get("tool_seconds", 0.0) or 0.0),
            "session": float(timing.get("session_seconds", 0.0) or 0.0),
            "workspace_setup": float(timing.get("prepare_workspace_seconds", 0.0) or 0.0),
        }
        return max(stages, key=lambda k: stages[k])

    def _category_stats(self, results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        stats: dict[str, dict[str, Any]] = {}
        for result in results:
            name = self._category_key(
                {"category": result.get("category", ""), "id": result.get("case_id", "")}
            )
            entry = stats.setdefault(
                name,
                {
                    "count": 0,
                    "wall": 0.0,
                    "slowest": 0.0,
                    "slowest_case": "",
                    "passed": 0,
                    "failed": [],
                    "skipped": [],
                    "infra": [],
                    "timeout": [],
                },
            )
            entry["count"] += 1
            seconds = float((result.get("timing") or {}).get("total_seconds") or result.get("latency_seconds") or 0.0)
            entry["wall"] += seconds
            if seconds > entry["slowest"]:
                entry["slowest"] = seconds
                entry["slowest_case"] = result.get("case_id", "")
            status = result.get("status")
            case_id = result.get("case_id", "")
            if status == "PASSED":
                entry["passed"] += 1
            elif status == "FAILED":
                entry["failed"].append(case_id)
            elif status == "SKIPPED":
                entry["skipped"].append(case_id)
            elif status == "TIMEOUT":
                entry["timeout"].append(case_id)
            elif status == "INFRASTRUCTURE_ERROR":
                entry["infra"].append(case_id)
        for entry in stats.values():
            entry["avg"] = entry["wall"] / entry["count"] if entry["count"] else 0.0
        return stats

    def run(
        self,
        case_ids: list[str] | None = None,
        category: str | None = None,
        progress: bool = True,
    ) -> list[dict[str, Any]]:
        self._ensure_fixture_seed()
        cases = self.load_cases()
        if case_ids:
            by_id = {c["id"]: c for c in cases}
            cases = [by_id[cid] for cid in case_ids if cid in by_id]
        if category:
            wanted = self._normalize_category(category)
            cases = [c for c in cases if self._category_key(c) == wanted]

        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

        if progress:
            provider_name, model_name = self._describe_provider(cases)
            print("WenDream Benchmark v1", flush=True)
            print(f"Provider: {provider_name}", flush=True)
            print(f"Model: {model_name}", flush=True)
            print(f"Total Cases: {len(cases)}", flush=True)
            print("-" * 60, flush=True)

        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            case_id = case.get("id", "LOCAL")
            cat = self._category_key(case)
            label = f"[{index:02d}/{len(cases):02d}]"
            if progress:
                print(f"{label} START {case_id} | category={cat}", flush=True)

            result = self.run_case_definition(case)
            results.append(result)

            elapsed = float(result.get("latency_seconds", 0.0) or 0.0)
            status = result.get("status", "UNKNOWN")
            if progress:
                if status == "PASSED":
                    print(f"{label} PASS {case_id} | {elapsed:.2f}s", flush=True)
                elif status == "FAILED":
                    print(
                        f"{label} FAIL {case_id} | {elapsed:.2f}s | reason={result.get('error')}",
                        flush=True,
                    )
                elif status == "SKIPPED":
                    print(
                        f"{label} SKIP {case_id} | reason={result.get('skip_reason') or result.get('error')}",
                        flush=True,
                    )
                elif status == "TIMEOUT":
                    print(
                        f"{label} TIMEOUT {case_id} | {elapsed:.2f}s | stage={result.get('timeout_stage') or 'unknown'}",
                        flush=True,
                    )
                else:
                    print(
                        f"{label} {status} {case_id} | reason={result.get('error')}",
                        flush=True,
                    )

                if elapsed > 60:
                    print(f"WARNING: VERY SLOW CASE {case_id} elapsed={elapsed:.2f}s stage={result.get('timeout_stage') or self._dominant_stage(result)}", flush=True)
                elif elapsed > 30:
                    print(f"WARNING: SLOW CASE {case_id} elapsed={elapsed:.2f}s stage={result.get('timeout_stage') or self._dominant_stage(result)}", flush=True)

                counts = result.get("counts") or {}
                if counts.get("llm_call_count", 0) > 5:
                    print(
                        f"WARNING: EXCESSIVE_AGENT_LOOP {case_id} llm_call_count={counts.get('llm_call_count')}",
                        flush=True,
                    )
                if counts.get("permission_request_count", 0) > 3:
                    print(
                        f"WARNING: EXCESSIVE_PERMISSION_REQUESTS {case_id} count={counts.get('permission_request_count')}",
                        flush=True,
                    )

        (self.results_dir / "latest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

        category_stats = self._category_stats(results)
        (self.results_dir / "timing.json").write_text(
            json.dumps(category_stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if progress:
            print("-" * 60, flush=True)
            print("Category summary", flush=True)
            for name, entry in category_stats.items():
                print(
                    f"  {name}: cases={entry['count']} wall={entry['wall']:.2f}s "
                    f"avg={entry['avg']:.2f}s slowest={entry['slowest_case']}({entry['slowest']:.2f}s)",
                    flush=True,
                )

        fieldnames = ["case_id", "category", "status", "passed_checks", "failed_checks", "session_id", "infrastructure_error", "total_seconds", "llm_call_count", "tool_call_count", "permission_request_count"]
        with (self.results_dir / "latest.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for result in results:
                counts = result.get("counts") or {}
                writer.writerow({
                    "case_id": result["case_id"],
                    "category": result.get("category", ""),
                    "status": result["status"],
                    "passed_checks": result["passed_checks"],
                    "failed_checks": len(result.get("failed_checks", [])),
                    "session_id": result.get("session_id", ""),
                    "infrastructure_error": result.get("infrastructure_error", False),
                    "total_seconds": (result.get("timing") or {}).get("total_seconds", ""),
                    "llm_call_count": counts.get("llm_call_count", 0),
                    "tool_call_count": counts.get("tool_call_count", 0),
                    "permission_request_count": counts.get("permission_request_count", 0),
                })

        total = len(results)
        passed = sum(1 for r in results if r["status"] == "PASSED")
        failed = sum(1 for r in results if r["status"] == "FAILED")
        skipped = sum(1 for r in results if r["status"] == "SKIPPED")
        infra = sum(1 for r in results if r["status"] == "INFRASTRUCTURE_ERROR")
        timed_out = sum(1 for r in results if r["status"] == "TIMEOUT")
        success_rate = (passed / (passed + failed)) * 100 if (passed + failed) else 0.0
        markdown = [
            "# Benchmark v1 Report",
            "",
            f"- Total cases: {total}",
            f"- PASSED: {passed}",
            f"- FAILED: {failed}",
            f"- SKIPPED: {skipped}",
            f"- TIMEOUT: {timed_out}",
            f"- INFRASTRUCTURE_ERROR: {infra}",
            f"- Task Success Rate: {success_rate:.1f}%",
            "",
            "## Summary by category",
        ]
        for name, entry in category_stats.items():
            markdown.append(
                f"- {name}: {entry['passed']}/{entry['count']} passed | "
                f"wall={entry['wall']:.2f}s avg={entry['avg']:.2f}s "
                f"slowest={entry['slowest_case']}({entry['slowest']:.2f}s)"
            )
            if entry["failed"]:
                markdown.append(f"    - failed: {', '.join(entry['failed'])}")
            if entry["skipped"]:
                markdown.append(f"    - skipped: {', '.join(entry['skipped'])}")
            if entry["infra"]:
                markdown.append(f"    - infrastructure_error: {', '.join(entry['infra'])}")
            if entry["timeout"]:
                markdown.append(f"    - timeout: {', '.join(entry['timeout'])}")
        markdown.append("")
        markdown.append("## Failed cases")
        for result in results:
            if result["status"] == "FAILED":
                markdown.append(f"- {result['case_id']}: {result.get('error') or 'evaluation mismatch'}")
        markdown.append("")
        markdown.append("## Skipped cases")
        for result in results:
            if result["status"] == "SKIPPED":
                markdown.append(f"- {result['case_id']}: {result.get('error') or 'skipped'}")
        markdown.append("")
        markdown.append("## Timed out cases")
        for result in results:
            if result["status"] == "TIMEOUT":
                markdown.append(f"- {result['case_id']}: {result.get('error') or 'agent turn timeout'}")
        markdown.append("")
        markdown.append("## Infrastructure errors")
        for result in results:
            if result["status"] == "INFRASTRUCTURE_ERROR":
                markdown.append(f"- {result['case_id']}: {result.get('error') or 'provider or infrastructure failure'}")
        (self.results_dir / "report.md").write_text("\n".join(markdown), encoding="utf-8")

        bad_cases = [
            result
            for result in results
            if result["status"] in {"FAILED", "SKIPPED", "TIMEOUT", "INFRASTRUCTURE_ERROR"}
        ]
        (self.results_dir / "bad_cases.md").write_text("\n".join(f"- {r['case_id']} | {r['status']} | {r.get('error') or 'n/a'}" for r in bad_cases), encoding="utf-8")
        return results

    def cli(self) -> None:
        parser = argparse.ArgumentParser(description="Run WenDream benchmark cases")
        parser.add_argument("--case-id", action="append", default=[], help="Restrict execution to one or more case IDs")
        parser.add_argument(
            "--category",
            default=None,
            help="Run only one category (context/file_operation/tool_calling/information/multimodal/session/error_recovery)",
        )
        parser.add_argument("--cases-path", default=None, help="Path to case catalog")
        parser.add_argument(
            "--version",
            choices=["v1", "v1_validated"],
            default="v1_validated",
            help="Which benchmark version to run (v1_validated is the validated baseline)",
        )
        args = parser.parse_args()

        if args.version == "v1_validated":
            from benchmark.runner_validated import ValidatedBenchmarkRunner

            runner = ValidatedBenchmarkRunner(cases_path=args.cases_path)
            runner.run(args.case_id or None)
            return

        self.cases_path = Path(args.cases_path) if args.cases_path else self.cases_path
        results = self.run(args.case_id, category=args.category, progress=True)
        passed = sum(1 for r in results if r.get("status") == "PASSED")
        print("-" * 60, flush=True)
        print(f"DONE total={len(results)} passed={passed}", flush=True)


if __name__ == "__main__":
    BenchmarkRunner().cli()
