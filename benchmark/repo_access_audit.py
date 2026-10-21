"""扫描 Benchmark trace，找出 Agent 访问 repo（case workspace 之外）的尝试与结果。

用法：
    python benchmark/repo_access_audit.py <traces_dir> [--json out.json]

判定：
* 只关心"绝对路径"以及 Bash 里的 `cd` / `PYTHONPATH=` 等对 repo 的引用；
* workspace 之外的 repo 路径 = 越界访问（repo access attempt）；
* attempt 的执行结果（is_error）决定它是否是成功的越界读取。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 抓取 Windows 绝对路径（含空格由引号包裹的情况另行处理）
_ABS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'|&;<>()]*")

# 允许被判为"workspace 内"的否定式：不是，workspace 本身也在 repo 里，
# 所以判定顺序必须是 先 workspace(inner) 再 repo。
WORKSPACES_DIR = (REPO_ROOT / "benchmark" / "workspaces").resolve()


def _norm(p: str) -> str:
    return str(p).replace("/", "\\").rstrip("\\").lower()


def classify_path(raw: str, workspace: Path) -> str:
    """返回 'workspace' | 'repo' | 'other'。"""
    text = _norm(raw)
    ws = _norm(str(workspace))
    repo = _norm(str(REPO_ROOT))
    if text == ws or text.startswith(ws + "\\"):
        return "workspace"
    if text == repo or text.startswith(repo + "\\"):
        return "repo"
    return "other"


def _pair_calls(calls: list[dict]) -> list[dict]:
    """把 arguments / result 两条记录按 tool_id 配对。"""
    out: list[dict] = []
    pending: dict[str, dict] = {}
    for tc in calls:
        tid = tc.get("tool_id")
        if tc.get("result") is not None or "elapsed" in tc:
            entry = pending.pop(tid, {"tool": tc.get("tool_name"), "args": {}})
            entry["is_error"] = tc.get("is_error")
            entry["elapsed"] = tc.get("elapsed")
            out.append(entry)
        else:
            pending[tid] = {"tool": tc.get("tool_name"), "args": tc.get("arguments") or {}}
    return out


def scan_trace(trace: dict) -> list[dict]:
    workspace = Path(str(trace.get("workspace") or ""))
    findings: list[dict] = []
    for call in _pair_calls(trace.get("tool_calls") or []):
        tool = call.get("tool")
        args = call.get("args") or {}
        candidates: list[str] = []
        if tool == "Bash":
            candidates = _ABS_PATH_RE.findall(str(args.get("command", "")))
        elif tool in ("ReadFile", "WriteFile", "EditFile"):
            candidates = [str(args.get("file_path", ""))]
        elif tool in ("Grep", "Glob"):
            candidates = [str(args.get("path", ""))]
        for raw in candidates:
            if not raw:
                continue
            where = classify_path(raw, workspace)
            if where == "repo":
                findings.append(
                    {
                        "tool": tool,
                        "path": raw,
                        "is_error": call.get("is_error"),
                        "args": args if tool != "Bash" else {"command": str(args.get("command", ""))[:160]},
                    }
                )
    return findings


def main() -> None:
    traces_dir = Path(sys.argv[1])
    rows: list[dict] = []
    for path in sorted(traces_dir.glob("*.json")):
        trace = json.loads(path.read_text(encoding="utf-8"))
        for finding in scan_trace(trace):
            finding["case_id"] = trace.get("case_id") or path.stem
            rows.append(finding)

    attempts = len(rows)
    successes = sum(1 for r in rows if not r["is_error"])
    print(f"traces: {traces_dir}")
    print(f"repo_access_attempts = {attempts}")
    print(f"repo_read_successes  = {successes}")
    print()
    by_case: dict[str, list[dict]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    for case_id, items in sorted(by_case.items()):
        ok = sum(1 for i in items if not i["is_error"])
        print(f"{case_id}: attempts={len(items)} successes={ok}")
        for item in items[:6]:
            flag = "OK " if not item["is_error"] else "ERR"
            print(f"    [{flag}] {item['tool']}: {json.dumps(item['args'], ensure_ascii=True)[:130]}")

    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwritten -> {out}")


if __name__ == "__main__":
    main()
