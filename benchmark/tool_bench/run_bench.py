"""Tool-level micro benchmark：测量 Grep / Glob 的延迟、扫描量与正确性。

不经过 LLM / Agent。

用法：
    python benchmark/tool_bench/run_bench.py --label before
    python benchmark/tool_bench/run_bench.py --label after
输出：
    benchmark/tool_bench/results/<label>.json
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import time
from pathlib import Path

from wendream.tools.glob import Glob
from wendream.tools.glob import Params as GlobParams
from wendream.tools.grep import Grep
from wendream.tools.grep import Params as GrepParams

BASE = Path(__file__).resolve().parent
FIXTURES = BASE / "fixtures"
RESULTS = BASE / "results"

TARGET = "WENDREAM_SEARCH_TARGET_2026"
ABSENT = "THIS_STRING_ABSENT_98765"
SCENARIO_TIMEOUT = 240.0

NOISE_DIRS = {"node_modules", ".git", "build", "dist", "__pycache__", ".conda", "venv"}


def _make(tool_cls, **kwargs):
    """兼容修复前（无 root 参数）与修复后（支持 root）。"""
    try:
        params = inspect.signature(tool_cls.__init__).parameters
        if "root" in params:
            return tool_cls(root=kwargs.pop("root"))
    except (TypeError, ValueError):
        pass
    return tool_cls()


def _stats_of(tool) -> dict:
    return dict(getattr(tool, "last_stats", {}) or {})


def _count_files(base: Path) -> int:
    try:
        return sum(1 for p in base.glob("**/*") if p.is_file())
    except Exception:
        return -1


async def _run_search(tool, params):
    return await asyncio.wait_for(tool.execute(params), timeout=SCENARIO_TIMEOUT)


def run_scenario(name: str, kind: str, root_name: str, path: str, pattern: str, include: str = "") -> dict:
    root = FIXTURES / root_name
    recorded: dict = {
        "scenario": name,
        "kind": kind,
        "root": root_name,
        "path": path,
        "pattern": pattern,
        "include": include,
    }
    if not root.exists():
        recorded["error"] = "fixture missing"
        return recorded

    tool = _make(Grep if kind == "grep" else Glob, root=str(root))
    if kind == "grep":
        params = GrepParams(pattern=pattern, path=path, include=include)
    else:
        params = GlobParams(pattern=pattern, path=path)

    previous_cwd = os.getcwd()
    started = time.perf_counter()
    try:
        # 修复前 "." 相对进程 cwd，这里把 cwd 固定到 fixture root，保持语义等价
        os.chdir(root)
        result = asyncio.run(_run_search(tool, params))
        elapsed = time.perf_counter() - started
        recorded.update(
            {
                "latency_s": round(elapsed, 3),
                "is_error": bool(result.is_error),
                "matches": 0 if "No matches" in result.output or "No files matched" in result.output
                else len([ln for ln in result.output.splitlines() if ln.strip()]),
                "output_chars": len(result.output),
                "timeout": False,
            }
        )
    except (asyncio.TimeoutError, TimeoutError):
        recorded.update(
            {
                "latency_s": round(time.perf_counter() - started, 3),
                "timeout": True,
                "is_error": True,
                "matches": 0,
            }
        )
    except Exception as exc:  # pragma: no cover
        recorded.update({"error": f"{type(exc).__name__}: {exc}", "is_error": True})
    finally:
        os.chdir(previous_cwd)

    stats = _stats_of(tool)
    if stats:
        recorded.update(stats)
    else:
        recorded["files_scanned"] = None
        recorded["directories_scanned"] = None
        recorded["bytes_scanned"] = None
        recorded["files_scanned_estimate"] = _count_files(root / path if path != "." else root)

    # 正确性：large 里 TARGET 只应出现在 src/target.py 与噪声诱饵中
    if kind == "grep" and pattern == TARGET and root_name == "large":
        recorded["noise_hits"] = sum(1 for d in NOISE_DIRS if f"{d}/" in "".join([recorded.get("_sample", "")]))
        recorded["expected_clean"] = "src/target.py only"
    return recorded


SCENARIOS = [
    ("grep_existing", "grep", "small", "src", TARGET, ""),
    ("grep_missing", "grep", "large", ".", ABSENT, ""),
    ("grep_dot_target", "grep", "large", ".", TARGET, ""),
    ("grep_subdir", "grep", "large", "pkg_3", "CONSTANT_", ""),
    ("glob_small", "glob", "small", ".", "**/*.py", ""),
    ("glob_large", "glob", "large", ".", "**/*.py", ""),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="before")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    records = []
    for name, kind, root_name, path, pattern, include in SCENARIOS:
        if args.only and name not in args.only:
            continue
        record = run_scenario(name, kind, root_name, path, pattern, include)
        records.append(record)
        print(
            f"{name:18} {kind:5} root={root_name:7} path={path:6} "
            f"latency={record.get('latency_s')}s matches={record.get('matches')} "
            f"files={record.get('files_scanned')} dirs={record.get('directories_scanned')} "
            f"bytes={record.get('bytes_scanned')} timeout={record.get('timeout')}",
            flush=True,
        )

    out = RESULTS / f"{args.label}.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten -> {out}")


if __name__ == "__main__":
    main()
