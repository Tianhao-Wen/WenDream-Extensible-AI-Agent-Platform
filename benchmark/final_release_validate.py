"""FINAL RELEASE VALIDATION 工具。

子命令：
    hash               生成/更新 benchmark/results/final_release/manifest.json
    run <subdir>       运行一轮全量 30 例，前后各记录一次代码哈希与仓库污染快照
    metrics [subdirs]  汇总每轮指标 + 稳定性指标（默认 run1 run2 run3）

设计约束：
  * 不重跑失败用例，不做任何 cherry-picking；
  * 代码哈希与 manifest 不一致时报错退出（0 退出码以外），避免把改动混进最终统计。
"""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 结果根目录可通过环境变量切换：默认 final_release（rename 前历史版本），
# 品牌迁移后请用 WENDREAM_RELEASE_DIR=final_release_wendream。
RELEASE_DIR_NAME = __import__("os").environ.get("WENDREAM_RELEASE_DIR", "final_release")
RELEASE_DIR = REPO_ROOT / "benchmark" / "results" / RELEASE_DIR_NAME
MANIFEST = RELEASE_DIR / "manifest.json"
CASES_PATH = REPO_ROOT / "benchmark" / "cases" / "benchmark_v1_validated.json"

KEY_FILES = [
    "wendream/__init__.py",
    "wendream/prompts.py",
    "wendream/agent.py",
    "wendream/config.py",
    "wendream/remote.py",
    "wendream/migration.py",
    "wendream/tools/__init__.py",
    "wendream/tools/bash.py",
    "wendream/tools/impl/tool_search.py",
    "wendream/tools/grep.py",
    "wendream/tools/glob.py",
    "wendream/tools/workspace.py",
    "wendream/tools/read_file.py",
    "wendream/tools/write_file.py",
    "wendream/tools/edit_file.py",
    "wendream/tools/encoding.py",
    "wendream/tools/search_utils.py",
    "benchmark/cases/benchmark_v1_validated.json",
    "benchmark/evaluator.py",
    "benchmark/runner_validated.py",
    "benchmark/session_flows.py",
    # hook pipeline（流式 pre_tool_use 修复的落点）
    "wendream/hooks/engine.py",
    "wendream/hooks/models.py",
    "wendream/hooks/executors.py",
    "wendream/hooks/conditions.py",
    # 打包与依赖锁
    "pyproject.toml",
    "uv.lock",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_hashes() -> dict[str, str]:
    return {rel: sha256(REPO_ROOT / rel) for rel in KEY_FILES}


def prompt_info() -> dict:
    from wendream.prompts import build_system_prompt

    prompt = build_system_prompt()
    headings = [
        line[2:].strip()
        for line in prompt.splitlines()
        if line.startswith("# ") and not line.startswith("## ")
    ]
    return {
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "chars": len(prompt),
        "lines": len(prompt.splitlines()),
        "sections": headings,
    }


def registry_info() -> dict:
    from wendream.tools import create_default_registry, lazy_explore_enabled

    work_dir = REPO_ROOT / "benchmark" / "workspaces" / "CTX_001"
    registry = create_default_registry(
        work_dir=str(work_dir), restrict_bash_to_workspace=True
    )
    return {
        "lazy_explore_tools": lazy_explore_enabled(),
        "env_WENDREAM_LAZY_EXPLORE_TOOLS": (
            __import__("os").environ.get("WENDREAM_LAZY_EXPLORE_TOOLS") or "<unset>"
        ),
        "restrict_bash_to_workspace": True,
        "exposed_tools": [s["name"] for s in registry.get_all_schemas()],
        "deferred_tools": registry.get_deferred_tool_names(),
    }


def run_model_info() -> dict:
    """从实际 trace 里读模型，而不是猜配置文件的第一个 provider。"""
    model = provider = protocol = base_url = context_window = "unknown"
    cfg_candidates = []
    for yml in (REPO_ROOT / ".wendream" / "config.yaml", Path.home() / ".wendream" / "config.yaml"):
        if yml.exists():
            cfg_candidates.append(yml)
    if cfg_candidates:
        text = cfg_candidates[0].read_text(encoding="utf-8")
        entries = []
        current: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("- name:"):
                if current:
                    entries.append(current)
                current = {"name": line.split(":", 1)[1].strip()}
            elif ":" in line and current and not line.startswith("#"):
                k, v = line.split(":", 1)
                current[k.strip()] = v.strip().strip('"')
        if current:
            entries.append(current)
        if entries:
            first = entries[0]
            model = first.get("model", model)
            protocol = first.get("protocol", protocol)
            base_url = first.get("base_url", base_url)
            context_window = first.get("context_window", context_window)
            provider = first.get("name", provider)
    observed = _observed_model_from_traces()
    return {
        "config_file": str(cfg_candidates[0]) if cfg_candidates else "<none>",
        "provider": provider,
        "protocol": protocol,
        "base_url": base_url,
        "model": model,
        "context_window": context_window,
        "observed_in_traces": observed,
    }


def _observed_model_from_traces() -> dict:
    """以实际运行生成的 trace 为准（配置里可能有多个 provider）。"""
    results = REPO_ROOT / "benchmark" / "results"
    candidates = sorted(results.glob("v2_phase2b3_tool_necessity/full_priority15/traces/*.json"))
    if not candidates:
        candidates = sorted(results.glob("*/full/traces/*.json"))
    seen: dict[str, int] = {}
    providers: dict[str, int] = {}
    for path in candidates[:30]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for c in data if isinstance(data, list) else [data]:
            if not isinstance(c, dict):
                continue
            m = str(c.get("model"))
            p = str(c.get("provider"))
            seen[m] = seen.get(m, 0) + 1
            providers[p] = providers.get(p, 0) + 1
    return {
        "source": str(candidates[0].parent) if candidates else "<none>",
        "models": seen,
        "providers": providers,
    }


def git_info() -> dict:
    def run(args: list[str]) -> str:
        try:
            proc = subprocess.run(
                args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20
            )
        except Exception as exc:  # noqa: BLE001
            return f"<{type(exc).__name__}: {exc}>"
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout).strip().splitlines()
            return f"<exit {proc.returncode}: {msg[0] if msg else ''}>"
        return proc.stdout.strip()

    return {
        "commit": run(["git", "rev-parse", "HEAD"]),
        "diff_stat": run(["git", "diff", "--stat"]),
        "available": run(["git", "rev-parse", "--is-inside-work-tree"]) == "true",
    }


def cmd_hash() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    case_list = cases.get("cases", cases) if isinstance(cases, dict) else cases
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "phase": "Phase 2B-3 Tool Necessity / Lazy Tool Exposure (FINAL, frozen)",
        "git": git_info(),
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "utf8_mode": int(sys.flags.utf8_mode),
        },
        "os": {
            "platform": f"{platform.system()} {platform.release()}",
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "provider": run_model_info(),
        "benchmark": {
            "case_count": len(case_list),
            "case_ids": [c.get("id") for c in case_list],
            "case_timeout_seconds": 180.0,
            "cases_sha256": sha256(CASES_PATH),
        },
        "system_prompt": prompt_info(),
        "tool_registry": registry_info(),
        "key_file_sha256": file_hashes(),
    }
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"manifest written -> {MANIFEST}")
    print(f"model={payload['provider']['model']} cases={payload['benchmark']['case_count']}")
    print(f"prompt_sha={payload['system_prompt']['sha256'][:16]}")
    print(f"tools exposed={payload['tool_registry']['exposed_tools']} "
          f"deferred={payload['tool_registry']['deferred_tools']}")


def _assert_manifest_matches() -> dict:
    if not MANIFEST.exists():
        raise SystemExit("manifest.json 缺失，请先运行 `hash`")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    recorded = dict(manifest["key_file_sha256"])
    current = file_hashes()
    diffs = {
        rel: (recorded.get(rel), current.get(rel))
        for rel in current
        if recorded.get(rel) != current.get(rel)
    }
    if diffs:
        for rel, (old, new) in diffs.items():
            print(f"DIFF {rel}: manifest={str(old)[:16]} now={str(new)[:16]}")
        raise SystemExit("代码已偏离冻结 manifest —— 拒绝混入最终统计")
    prompt_now = prompt_info()["sha256"]
    if prompt_now != manifest["system_prompt"]["sha256"]:
        raise SystemExit("system prompt 已变化 —— 拒绝混入最终统计")
    if sha256(CASES_PATH) != manifest["benchmark"]["cases_sha256"]:
        raise SystemExit("benchmark case 文件已变化 —— 拒绝混入最终统计")
    print("frozen-code check: OK (all hashes match manifest)")
    return manifest


def cmd_run(subdir: str) -> None:
    manifest = _assert_manifest_matches()
    from benchmark.pollution_audit import snapshot
    from benchmark.runner_validated import ValidatedBenchmarkRunner

    run_dir = RELEASE_DIR / subdir
    run_dir.mkdir(parents=True, exist_ok=True)

    pre = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "key_file_sha256": file_hashes(),
        "system_prompt_sha256": prompt_info()["sha256"],
        "cases_sha256": sha256(CASES_PATH),
        "repo_snapshot_count": len(snapshot()),
    }
    (run_dir / "hashes_pre.json").write_text(
        json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    before = snapshot()

    runner = ValidatedBenchmarkRunner(
        base_dir=str(REPO_ROOT),
        # 必须跟随 RELEASE_DIR_NAME，否则在 WENDREAM_RELEASE_DIR 切换时会写进历史目录
        results_dir_name=f"{RELEASE_DIR_NAME}/{subdir}",
        case_timeout_seconds=180.0,
    )
    runner.run(None)

    after = snapshot()
    post = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "key_file_sha256": file_hashes(),
        "system_prompt_sha256": prompt_info()["sha256"],
        "cases_sha256": sha256(CASES_PATH),
    }
    (run_dir / "hashes_post.json").write_text(
        json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    created = sorted(set(after) - set(before))
    modified = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    removed = sorted(set(before) - set(after))
    pollution = {
        "created": created,
        "modified": modified,
        "removed": removed,
        "clean": not (created or modified or removed),
    }
    (run_dir / "pollution.json").write_text(
        json.dumps(pollution, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    changed = {
        rel: (pre["key_file_sha256"][rel], post["key_file_sha256"][rel])
        for rel in pre["key_file_sha256"]
        if pre["key_file_sha256"][rel] != post["key_file_sha256"][rel]
    }
    if changed or pre["system_prompt_sha256"] != post["system_prompt_sha256"]:
        raise SystemExit(f"运行期间代码/提示发生变化: {changed}")
    print(f"run [{subdir}] done | code unchanged | pollution_clean={pollution['clean']}")
    if not pollution["clean"]:
        print("  created:", created[:10], "modified:", modified[:10])


# ---------------------------------------------------------------- metrics


def _load_traces(run_dir: Path) -> list[dict]:
    rows = []
    for f in sorted((run_dir / "traces").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for c in data if isinstance(data, list) else [data]:
            if isinstance(c, dict):
                rows.append(c)
    return rows


def _case_latency(trace: dict) -> float:
    timing = trace.get("timing") or {}
    for key in ("total_seconds", "elapsed_seconds", "elapsed", "total"):
        if timing.get(key) is not None:
            return float(timing[key])
    return float(trace.get("elapsed_seconds") or 0.0)


def _wall_seconds(run_dir: Path) -> float:
    report = run_dir / "report.md"
    if not report.exists():
        return 0.0
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith("- Total wall time:"):
            return float(line.split(":", 1)[1].strip().rstrip("s"))
    return 0.0


def compute_metrics(run_dir: Path) -> dict:
    from benchmark.analyze_efficiency import tools_needed

    cases = {
        c["id"]: c
        for c in json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    }
    traces = _load_traces(run_dir)

    statuses = Counter()
    latencies: list[float] = []
    llm_counts: list[int] = []
    tool_counts: list[int] = []
    free_cases: list[tuple[str, int]] = []
    undecodable = 0

    for t in traces:
        status = str(t.get("status") or "").upper() or "UNKNOWN"
        statuses[status] += 1
        if status.startswith("SKIPPED"):
            continue
        counts = t.get("counts") or {}
        llm = int(counts.get("llm_call_count") or 0)
        tool = int(counts.get("tool_call_count") or 0)
        llm_counts.append(llm)
        tool_counts.append(tool)
        latencies.append(_case_latency(t))
        for result in t.get("tool_results") or []:
            if "\ufffd" in str(result.get("output") or ""):
                undecodable += 1
        for call in t.get("tool_calls") or []:
            if "\ufffd" in str(call.get("result") or ""):
                undecodable += 1
        cid = t.get("case_id")
        case = cases.get(cid)
        if case is not None and not tools_needed(case):
            free_cases.append((cid, tool))

    passed = statuses.get("PASSED", 0)
    failed = statuses.get("FAILED", 0)
    timeout = statuses.get("TIMEOUT", 0)
    skipped = sum(v for k, v in statuses.items() if k.startswith("SKIPPED"))
    denominator = passed + failed + timeout

    eff: dict = {}
    for candidate in (
        run_dir / "efficiency" / "agent_efficiency_baseline.json",
        run_dir.parent / "efficiency_recomputed" / "agent_efficiency_baseline.json",
        run_dir.parent / "efficiency" / "agent_efficiency_baseline.json",
    ):
        if candidate.exists():
            eff = json.loads(candidate.read_text(encoding="utf-8"))
            break
    if eff.get("undecodable_tool_results") is not None:
        undecodable = int(eff["undecodable_tool_results"])

    compliant = sum(1 for _, n in free_cases if n == 0)
    pollution_path = run_dir / "pollution.json"
    pollution = (
        json.loads(pollution_path.read_text(encoding="utf-8"))
        if pollution_path.exists()
        else {"clean": None, "created": [], "modified": [], "removed": []}
    )

    return {
        "run": run_dir.name,
        "total": len(traces),
        "passed": passed,
        "failed": failed,
        "timeout": timeout,
        "skipped": skipped,
        "task_success_rate": round(passed / denominator * 100, 1) if denominator else 0.0,
        "wall_seconds": round(_wall_seconds(run_dir), 2),
        "sum_case_latency": round(sum(latencies), 2),
        "avg_latency": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "median_latency": round(statistics.median(latencies), 2) if latencies else 0.0,
        "avg_llm": round(statistics.mean(llm_counts), 2) if llm_counts else 0.0,
        "median_llm": statistics.median(llm_counts) if llm_counts else 0,
        "avg_tool": round(statistics.mean(tool_counts), 2) if tool_counts else 0.0,
        "median_tool": statistics.median(tool_counts) if tool_counts else 0,
        "post_completion_llm": eff.get("post_completion_llm_calls"),
        "post_completion_tool": eff.get("post_completion_tool_calls"),
        "over_verification": eff.get("over_verification_count"),
        "unnecessary_tool_calls": eff.get("unnecessary_tool_calls"),
        "unnecessary_tool_rate": eff.get("unnecessary_tool_rate"),
        "tool_free_compliant": compliant,
        "tool_free_total": len(free_cases),
        "tool_free_compliance_rate": round(compliant / len(free_cases) * 100, 1)
        if free_cases
        else 0.0,
        "tool_free_calls": sum(n for _, n in free_cases),
        "tool_free_detail": {cid: n for cid, n in free_cases},
        "undecodable_tool_results": undecodable,
        "repo_pollution_clean": pollution.get("clean"),
        "repo_pollution_created": len(pollution.get("created") or []),
        "repo_pollution_modified": len(pollution.get("modified") or []),
        "non_passing": {
            str(t.get("case_id")): f"{t.get('status')} | {t.get('failure_reason') or ''}".strip(" |")
            for t in traces
            if str(t.get("status") or "").upper() in {"FAILED", "TIMEOUT"}
        },
    }


def _flaky_analysis(run_dirs: list[Path]) -> dict:
    per_case: dict[str, dict] = {}
    for run_dir in run_dirs:
        for t in _load_traces(run_dir):
            cid = str(t.get("case_id"))
            status = str(t.get("status") or "").upper()
            counts = t.get("counts") or {}
            per_case.setdefault(cid, {})[run_dir.name] = {
                "status": status,
                "llm": int(counts.get("llm_call_count") or 0),
                "tool": int(counts.get("tool_call_count") or 0),
                "path": [
                    (c.get("tool_name") or c.get("name"))
                    for c in (t.get("tool_calls") or [])
                ],
            }

    flaky_status = []
    flaky_path = []
    variance = []
    for cid, runs in sorted(per_case.items()):
        statuses = [r["status"] for r in runs.values()]
        if len(set(statuses)) > 1:
            flaky_status.append(cid)
        paths = [tuple(r["path"]) for r in runs.values()]
        if len({p for p in paths}) > 1:
            flaky_path.append(cid)
        llms = [r["llm"] for r in runs.values()]
        tools = [r["tool"] for r in runs.values()]
        variance.append(
            {
                "case_id": cid,
                "statuses": statuses,
                "llm": llms,
                "tool": tools,
                "llm_spread": max(llms) - min(llms),
                "tool_spread": max(tools) - min(tools),
            }
        )
    executed = [v for v in variance if not all(s.startswith("SKIPPED") for s in v["statuses"])]
    return {
        "runs": [d.name for d in run_dirs],
        "cases": len(variance),
        "status_flaky_cases": flaky_status,
        "status_flaky_rate": round(len(flaky_status) / len(variance) * 100, 1) if variance else 0.0,
        "tool_path_flaky_cases": flaky_path,
        "tool_path_flaky_rate": round(len(flaky_path) / len(variance) * 100, 1) if variance else 0.0,
        "avg_llm_spread": round(statistics.mean(v["llm_spread"] for v in executed), 2)
        if executed
        else 0.0,
        "avg_tool_spread": round(statistics.mean(v["tool_spread"] for v in executed), 2)
        if executed
        else 0.0,
        "max_llm_spread": max((v["llm_spread"] for v in executed), default=0),
        "max_tool_spread": max((v["tool_spread"] for v in executed), default=0),
        "per_case": variance,
    }


def _agg(values: list[float]) -> dict:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(min(values), 2),
        "median": round(statistics.median(values), 2),
        "max": round(max(values), 2),
    }


def cmd_metrics(subdirs: list[str], official_count: int) -> None:
    run_dirs = [RELEASE_DIR / s for s in subdirs]
    metrics = []
    for run_dir in run_dirs:
        if not (run_dir / "traces").exists():
            raise SystemExit(f"missing traces: {run_dir}")
        m = compute_metrics(run_dir)
        (run_dir / "metrics.json").write_text(
            json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        metrics.append(m)

    official = metrics[:official_count]
    official_dirs = run_dirs[:official_count]

    keys = [
        "task_success_rate",
        "wall_seconds",
        "median_latency",
        "avg_latency",
        "avg_llm",
        "median_llm",
        "avg_tool",
        "median_tool",
        "tool_free_compliance_rate",
        "post_completion_llm",
        "post_completion_tool",
        "over_verification",
        "unnecessary_tool_calls",
        "undecodable_tool_results",
    ]
    stability = {
        "official_runs": [m["run"] for m in official],
        "audit_runs": [m["run"] for m in metrics[official_count:]],
        "aggregate": {
            key: _agg([m[key] for m in official if m.get(key) is not None]) for key in keys
        },
        "flaky": _flaky_analysis(official_dirs),
        "per_run": official,
        "audit": metrics[official_count:],
    }
    (RELEASE_DIR / "stability.json").write_text(
        json.dumps(stability, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"{'run':12s} {'succ':>6s} {'P/F/T/S':>12s} {'wall':>8s} {'medLat':>7s} "
          f"{'avgLLM':>7s} {'avgTool':>8s} {'postL/T':>9s} {'OV':>3s} {'unnec':>6s} "
          f"{'TFcomp':>7s} {'U+FFFD':>7s} {'poll':>5s}")
    for m in metrics:
        print(
            f"{m['run']:12s} {m['task_success_rate']:6.1f} "
            f"{m['passed']}/{m['failed']}/{m['timeout']}/{m['skipped']:<6} "
            f"{m['wall_seconds']:8.2f} {m['median_latency']:7.2f} "
            f"{m['avg_llm']:7.2f} {m['avg_tool']:8.2f} "
            f"{str(m['post_completion_llm']) + '/' + str(m['post_completion_tool']):>9s} "
            f"{str(m['over_verification']):>3s} {str(m['unnecessary_tool_calls']):>6s} "
            f"{str(m['tool_free_compliant']) + '/' + str(m['tool_free_total']):>7s} "
            f"{m['undecodable_tool_results']:7d} {str(m['repo_pollution_clean']):>5s}"
        )
    print("\nstability (official runs only):")
    for key, agg in stability["aggregate"].items():
        print(f"  {key:26s} min={agg['min']} median={agg['median']} max={agg['max']}")
    print(f"\nstability.json -> {RELEASE_DIR / 'stability.json'}")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "hash"
    if action == "hash":
        cmd_hash()
    elif action == "run":
        cmd_run(sys.argv[2])
    elif action == "metrics":
        subs = [s for s in sys.argv[2:] if not s.startswith("--")]
        official = 3
        for arg in sys.argv[2:]:
            if arg.startswith("--official="):
                official = int(arg.split("=", 1)[1])
        subs = subs or ["run1", "run2", "run3"]
        cmd_metrics(subs, official)
    else:
        raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    main()
