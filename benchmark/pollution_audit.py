"""仓库污染审计：确认 Benchmark 运行期间没有在 workspace 之外写入文件。

用法：
    python benchmark/pollution_audit.py snapshot before.json
    python benchmark/pollution_audit.py compare before.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 这些目录本来就承载运行产物，不属于"污染"
EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".conda",
    "conda-meta",
    "dist",
    "build",
    "site-packages",
    ".wendream",
    "results",
    "workspaces",
    "fixtures",
}

# 允许变化的路径前缀（相对仓库根，posix 风格）
EXCLUDE_PREFIXES = (
    "benchmark/results",
    "benchmark/workspaces",
    "benchmark/tool_bench/fixtures",
    "benchmark/tool_bench/results",
)


def _excluded(rel_posix: str) -> bool:
    for prefix in EXCLUDE_PREFIXES:
        if rel_posix == prefix or rel_posix.startswith(prefix + "/"):
            return True
    return False


def snapshot() -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                continue
            if _excluded(rel):
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            out[rel] = (st.st_size, st.st_mtime_ns)
    return out


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    target = Path(sys.argv[2] if len(sys.argv) > 2 else "before.json")

    current = snapshot()
    if action == "snapshot":
        target.write_text(json.dumps(current, indent=0), encoding="utf-8")
        print(f"snapshot written: {target} ({len(current)} files)")
        return

    if not target.exists():
        print(f"missing baseline: {target}")
        raise SystemExit(2)

    before = json.loads(target.read_text(encoding="utf-8"))
    before = {k: tuple(v) for k, v in before.items()}

    created = sorted(set(current) - set(before))
    removed = sorted(set(before) - set(current))
    modified = sorted(k for k in set(current) & set(before) if current[k] != before[k])

    print(f"baseline files: {len(before)} | now: {len(current)}")
    print(f"created: {len(created)} | modified: {len(modified)} | removed: {len(removed)}")
    for label, items in (("CREATED", created), ("MODIFIED", modified), ("REMOVED", removed)):
        for item in items[:40]:
            print(f"  {label}: {item}")
    if created or modified or removed:
        raise SystemExit(1)
    print("CLEAN: repository outside workspace is untouched")


if __name__ == "__main__":
    main()
