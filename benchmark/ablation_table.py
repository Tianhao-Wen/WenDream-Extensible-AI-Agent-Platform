"""生成 Phase 1c / 2B-1 / 2B-2 / 2B-3 四阶段消融表（数据全部来自落盘结果，不手填）。"""

import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.analyze_efficiency import tools_needed  # noqa: E402

PHASES = [
    ("Phase 1c isolation", "v2_phase1c_isolation", "full"),
    ("Phase 2B-1 encoding", "v2_phase2b1_encoding", "full"),
    ("Phase 2B-2 early stop", "v2_phase2b2_early_stop", "full"),
    ("Phase 2B-3 tool necessity", "v2_phase2b3_tool_necessity", "full_priority15"),
]

CASES = {
    c["id"]: c
    for c in json.loads(
        (REPO_ROOT / "benchmark" / "cases" / "benchmark_v1_validated.json").read_text(
            encoding="utf-8"
        )
    )["cases"]
}


def load_traces(traces_dir: Path) -> list[dict]:
    rows = []
    for f in sorted(traces_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for c in data if isinstance(data, list) else [data]:
            if isinstance(c, dict):
                rows.append(c)
    return rows


def collect(dirname: str, sub: str) -> dict:
    base = REPO_ROOT / "benchmark" / "results" / dirname / sub
    report = (base / "report.md").read_text(encoding="utf-8")
    traces = load_traces(base / "traces")

    def field(name: str) -> str:
        for line in report.splitlines():
            stripped = line.strip().lstrip("-* ").strip()
            if not stripped.startswith(name):
                continue
            for sep in (":", "="):
                if sep in stripped:
                    return stripped.split(sep, 1)[1].strip().strip("*").strip()
        return "?"

    eff_path = REPO_ROOT / "benchmark" / "results" / dirname / "efficiency_recomputed" / "agent_efficiency_baseline.json"
    if not eff_path.exists():
        eff_path = (REPO_ROOT / "benchmark" / "results" / "v2_phase2b3_tool_necessity"
                    / "efficiency_priority15" / "agent_efficiency_baseline.json")
    eff = json.loads(eff_path.read_text(encoding="utf-8"))

    free_cases = []
    free_calls = 0
    for c in traces:
        if str(c.get("status", "")).startswith("SKIPPED"):
            continue
        cid = c.get("case_id")
        case = CASES.get(cid)
        if case is None or tools_needed(case):
            continue
        n = (c.get("counts") or {}).get("tool_call_count") or 0
        free_calls += n
        free_cases.append(n)

    tool_calls_total = sum(
        (c.get("counts") or {}).get("tool_call_count") or 0
        for c in traces
        if not str(c.get("status", "")).startswith("SKIPPED")
    )
    undecodable = sum(
        1
        for c in traces
        for r in (c.get("tool_results") or [])
        if "\ufffd" in str(r.get("output") or "")
    )
    latencies = [
        float((c.get("timing") or {}).get("elapsed") or c.get("elapsed_seconds") or 0.0)
        for c in traces
        if not str(c.get("status", "")).startswith("SKIPPED")
    ]

    return {
        "success": field("Task Success Rate").split("=")[-1].strip(),
        "passed": field("PASSED"),
        "failed": field("FAILED"),
        "timeout": field("TIMEOUT"),
        "wall": field("Total wall time"),
        "avg_latency": field("Average latency"),
        "avg_llm": field("Average LLM calls"),
        "avg_tool": field("Average Tool calls"),
        "eff_llm": eff["avg_llm_calls"],
        "eff_tool": eff["avg_tool_calls"],
        "eff_unnecessary": eff.get("unnecessary_tool_rate"),
        "post_llm": eff.get("post_completion_llm_calls"),
        "post_tool": eff.get("post_completion_tool_calls"),
        "over_verification": eff.get("over_verification_count"),
        "undecodable": eff.get("undecodable_tool_results"),
        "median_latency_eff": eff.get("median_latency"),
        "tool_free_calls": free_calls,
        "tool_free_compliant": sum(1 for n in free_cases if n == 0),
        "tool_free_total": len(free_cases),
        "tool_calls_total": tool_calls_total,
        "undecodable_traces": undecodable,
        "median_latency": round(statistics.median(latencies), 2) if latencies else 0.0,
        "median_llm": eff.get("median_llm_calls"),
        "median_tool": eff.get("median_tool_calls"),
    }


def main() -> None:
    rows = []
    for label, dirname, sub in PHASES:
        rows.append((label, collect(dirname, sub)))

    keys = [
        ("Task Success", "success"),
        ("PASSED/FAILED/TIMEOUT", None),
        ("Wall (s)", "wall"),
        ("Median latency (s)", "median_latency_eff"),
        ("Avg LLM", "eff_llm"),
        ("Median LLM", "median_llm"),
        ("Avg Tool", "eff_tool"),
        ("Median Tool", "median_tool"),
        ("Unnecessary tool rate (%)", "eff_unnecessary"),
        ("Tool-free compliance", None),
        ("Tool calls in tool-free cases", "tool_free_calls"),
        ("Post-completion LLM", "post_llm"),
        ("Post-completion Tool", "post_tool"),
        ("OVER_VERIFICATION", "over_verification"),
        ("U+FFFD tool results", "undecodable"),
    ]

    lines = ["| 指标 | " + " | ".join(label for label, _ in rows) + " |",
             "|---" * (len(rows) + 1) + "|"]
    for name, key in keys:
        cells = []
        for _, d in rows:
            if name == "PASSED/FAILED/TIMEOUT":
                cells.append(f"{d['passed']}/{d['failed']}/{d['timeout']}")
            elif name == "Tool-free compliance":
                cells.append(f"{d['tool_free_compliant']}/{d['tool_free_total']}")
            else:
                cells.append(str(d[key]))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    table = "\n".join(lines)
    print(table)

    out = REPO_ROOT / "benchmark" / "results" / "v2_phase2b3_tool_necessity" / "ablation_table.md"
    out.write_text("# 四阶段消融表（同一分析器口径）\n\n" + table + "\n", encoding="utf-8")
    print("\nwritten ->", out)


if __name__ == "__main__":
    main()
