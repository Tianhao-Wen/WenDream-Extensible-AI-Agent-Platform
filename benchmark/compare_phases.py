"""三方结果对比：v1_validated/full vs v2_phase1_tools vs v2_phase1b_workspace/full。

用法：python benchmark/compare_phases.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"

TARGETS = [
    ("baseline(v1_validated)", RESULTS / "v1_validated" / "full"),
    ("phase1(tools)", RESULTS / "v2_phase1_tools"),
    ("phase1b(workspace)", RESULTS / "v2_phase1b_workspace"),
    ("phase1b(full30)", RESULTS / "v2_phase1b_workspace" / "full"),
]


def load_csv(dir_path: Path) -> dict[str, dict]:
    csv_path = dir_path / "latest.csv"
    if not csv_path.exists():
        return {}
    with csv_path.open(encoding="utf-8", newline="") as fh:
        return {row["case_id"]: row for row in csv.DictReader(fh)}


def summarize(dir_path: Path) -> dict:
    rows = load_csv(dir_path)
    if not rows:
        return {}
    counts: dict[str, int] = {}
    total = 0.0
    for row in rows.values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        try:
            total += float(row["latency_seconds"] or 0)
        except ValueError:
            pass
    n = len(rows)
    passed = counts.get("PASSED", 0)
    failed = counts.get("FAILED", 0)
    timeout = counts.get("TIMEOUT", 0)
    denom = passed + failed + timeout
    return {
        "n": n,
        "passed": passed,
        "failed": failed,
        "timeout": timeout,
        "skipped": counts.get("SKIPPED", 0),
        "infra": counts.get("INFRASTRUCTURE_ERROR", 0) + counts.get("EVALUATOR_ERROR", 0),
        "success_rate": (passed / denom * 100) if denom else 0.0,
        "wall_sum": total,
        "rows": rows,
    }


def main() -> None:
    print(f"{'phase':<24}{'n':>4}{'PASS':>6}{'FAIL':>6}{'TIME':>6}{'SKIP':>6}{'SUCC%':>8}{'SUM(s)':>10}")
    print("-" * 70)
    loaded = []
    for label, path in TARGETS:
        s = summarize(path)
        if not s:
            print(f"{label:<24}  (missing: {path.name})")
            continue
        loaded.append((label, s))
        print(
            f"{label:<24}{s['n']:>4}{s['passed']:>6}{s['failed']:>6}{s['timeout']:>6}"
            f"{s['skipped']:>6}{s['success_rate']:>8.1f}{s['wall_sum']:>10.1f}"
        )

    if len(loaded) < 2:
        return

    base_label, base = loaded[0]
    print("\n## Per-case latency comparison (seconds, with status)\n")
    header = f"{'case':<12}"
    for label, _ in loaded:
        header += f"{label[:20]:>22}"
    print(header)
    for case_id in sorted(set().union(*(set(s['rows']) for _, s in loaded))):
        line = f"{case_id:<12}"
        for _, s in loaded:
            row = s["rows"].get(case_id)
            if row is None:
                line += f"{'-':>22}"
            else:
                cell = f"{float(row['latency_seconds']):.1f} {row['status'][:4].lower()}"
                line += f"{cell:>22}"
        print(line)


if __name__ == "__main__":
    main()
