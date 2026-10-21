"""计算 Tool-Free Compliance Rate：needs_tools=false 的用例里，完全没有工具调用的比例。

needs_tools 只来自离线 benchmark 元数据（analyze_efficiency.tools_needed），运行时从不暴露。
"""

import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.analyze_efficiency import tools_needed  # noqa: E402

PHASES = [
    ("phase1c_isolation", "v2_phase1c_isolation"),
    ("phase2b1_encoding", "v2_phase2b1_encoding"),
    ("phase2b2_early_stop", "v2_phase2b2_early_stop"),
    ("phase2b3_tool_necessity", "v2_phase2b3_tool_necessity"),
]


def load_cases() -> dict:
    data = json.loads(
        (REPO_ROOT / "benchmark" / "cases" / "benchmark_v1_validated.json").read_text(
            encoding="utf-8"
        )
    )
    cases = data.get("cases", data) if isinstance(data, dict) else data
    return {c["id"]: c for c in cases}


def main() -> None:
    cases = load_cases()
    print(f"{'phase':26s} {'tool-free cases':34s} {'calls':>5s} {'compliant':>9s} {'rate':>6s}  {'avg_llm':>7s} {'avg_tool':>8s}")
    for label, dirname in PHASES:
        traces_dir = REPO_ROOT / "benchmark" / "results" / dirname / "full" / "traces"
        if not traces_dir.exists():
            print(f"{label:26s} (missing)")
            continue
        rows = []
        for f in sorted(traces_dir.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            for c in data if isinstance(data, list) else [data]:
                rows.append(c)

        free_ids = []
        calls_total = 0
        compliant = 0
        per_case = []
        llm_all = []
        tool_all = []
        for c in rows:
            cid = c.get("case_id") or c.get("id")
            case = cases.get(cid)
            if case is None:
                continue
            status = str(c.get("status") or "")
            if status.upper().startswith("SKIPPED"):
                continue
            counts = c.get("counts") or {}
            tool_calls = counts.get("tool_call_count")
            llm = counts.get("llm_call_count")
            if tool_calls is None:
                tool_calls = len(c.get("tool_calls") or [])
            if llm is not None:
                llm_all.append(llm)
            tool_all.append(tool_calls)
            if tools_needed(case):
                continue
            free_ids.append(cid)
            calls_total += tool_calls
            if tool_calls == 0:
                compliant += 1
            per_case.append(f"{cid}={tool_calls}")

        rate = (compliant / len(free_ids) * 100) if free_ids else 0.0
        print(
            f"{label:26s} {','.join(per_case):34s} {calls_total:5d} "
            f"{compliant}/{len(free_ids):<7d} {rate:5.1f}%  "
            f"{statistics.mean(llm_all):7.2f} {statistics.mean(tool_all):8.2f}"
        )


if __name__ == "__main__":
    main()
