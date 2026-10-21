"""生成 FINAL RELEASE 消融表（7 阶段全量 + README 用 4 阶段）。

所有数字都来自各阶段落盘的 traces / report.md，用同一套 compute_metrics 计算，
不手填。Final 一列使用 3 次正式运行的 MEDIAN。
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.final_release_validate import compute_metrics  # noqa: E402

RESULTS = REPO_ROOT / "benchmark" / "results"

STAGES = [
    ("Baseline (v1_validated)", "v1_validated"),
    ("Phase 1: grep/glob runtime", "v2_phase1_tools"),
    ("Phase 1b: workspace semantics", "v2_phase1b_workspace"),
    ("Phase 1c: benchmark isolation", "v2_phase1c_isolation"),
    ("Phase 2B-1: bash encoding", "v2_phase2b1_encoding"),
    ("Phase 2B-2: early stop", "v2_phase2b2_early_stop"),
    ("Phase 2B-3: lazy tool exposure", "final_release"),
]

README_STAGES = [
    "Baseline (v1_validated)",
    "Phase 2B-1: bash encoding",
    "Phase 2B-2: early stop",
    "Phase 2B-3: lazy tool exposure",
]


def stage_metrics(dirname: str) -> dict:
    if dirname == "final_release":
        runs = ["run1", "run2", "run3"]
        each = [compute_metrics(RESULTS / dirname / r) for r in runs]
        med: dict = {"run": "final_release (median of 3)"}
        for key in (
            "task_success_rate",
            "wall_seconds",
            "median_latency",
            "avg_latency",
            "avg_llm",
            "avg_tool",
            "tool_free_compliance_rate",
            "post_completion_llm",
            "post_completion_tool",
            "over_verification",
            "unnecessary_tool_calls",
            "undecodable_tool_results",
        ):
            values = [m[key] for m in each if m.get(key) is not None]
            med[key] = round(statistics.median(values), 2) if values else None
        med["runs"] = each
        med["range_success"] = [min(m["task_success_rate"] for m in each),
                                max(m["task_success_rate"] for m in each)]
        med["range_wall"] = [min(m["wall_seconds"] for m in each),
                             max(m["wall_seconds"] for m in each)]
        for key in ("total", "passed", "failed", "timeout", "skipped"):
            med[key] = statistics.median([m[key] for m in each])
        for key in ("tool_free_compliant", "tool_free_total", "tool_free_calls"):
            med[key] = statistics.median([m[key] for m in each])
        med["undecodable_tool_results"] = int(
            statistics.median([m["undecodable_tool_results"] for m in each])
        )
        return med

    base = RESULTS / dirname
    if not (base / "full").exists() and (base / "traces").exists():
        base = base
    elif (base / "full").exists():
        base = base / "full"
    m = compute_metrics(base)
    if not (base / "efficiency").exists():
        # 历史阶段若未生成效率分析，post-completion / OV 允许为 None
        pass
    return m


def fmt(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build_table(stage_names: list[str], data: dict) -> str:
    rows = [
        ("Task Success Rate", lambda d: (f"{d['task_success_rate']:.1f}%"
                                         + (f" ({d['range_success'][0]:.1f}–{d['range_success'][1]:.1f}%)"
                                            if d.get("range_success") else ""))),
        ("TIMEOUT", lambda d: str(d["timeout"])),
        ("median latency (s)", lambda d: fmt(d["median_latency"])),
        ("avg LLM calls", lambda d: fmt(d["avg_llm"])),
        ("avg Tool calls", lambda d: fmt(d["avg_tool"])),
        ("Tool-Free Compliance", lambda d: f"{d['tool_free_compliant']}/{d['tool_free_total']}"
                                          if d.get("tool_free_total") else "—"),
        ("OVER_VERIFICATION", lambda d: str(d["over_verification"]) if d.get("over_verification") is not None else "—"),
        ("U+FFFD tool results", lambda d: str(d["undecodable_tool_results"])),
    ]
    header = "| 指标 | " + " | ".join(stage_names) + " |"
    sep = "|---" * (len(stage_names) + 1) + "|"
    lines = [header, sep]
    for label, getter in rows:
        cells = [getter(data[name]) for name in stage_names]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    data = {}
    for name, dirname in STAGES:
        d = stage_metrics(dirname)
        d["stage"] = name
        d["dir"] = dirname
        data[name] = d
        partial = "" if d.get("total", 30) >= 30 else f" (partial run: {d['total']}/30 cases)"
        print(
            f"{name:32s} succ={d['task_success_rate']:5.1f}% "
            f"P/F/T={d['passed']}/{d['failed']}/{d['timeout']} "
            f"wall={fmt(d['wall_seconds'], 2)} medLat={fmt(d['median_latency'])} "
            f"avgLLM={fmt(d['avg_llm'])} avgTool={fmt(d['avg_tool'])} "
            f"TF={d['tool_free_compliant']}/{d['tool_free_total']} "
            f"OV={d.get('over_verification')} FFFD={d['undecodable_tool_results']}"
            f"{partial}"
        )

    full = build_table([n for n, _ in STAGES], data)
    readme = build_table([n for n in README_STAGES], data)

    out = RESULTS / "final_release" / "ablation_final.md"
    out.write_text(
        "# Final Ablation（同一分析器口径）\n\n"
        "## 全阶段\n\n" + full + "\n\n"
        "## README 四阶段版\n\n" + readme + "\n\n"
        "注：`Phase 2B-1` 一列同时承载了此前的 search runtime / workspace semantics / benchmark isolation 等基础设施修复，"
        "并非“仅编码修复”的净效应；逐阶段差分请见全阶段表。\n",
        encoding="utf-8",
    )
    (RESULTS / "final_release" / "ablation_final.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + readme)
    print("\nwritten ->", out)


if __name__ == "__main__":
    main()
