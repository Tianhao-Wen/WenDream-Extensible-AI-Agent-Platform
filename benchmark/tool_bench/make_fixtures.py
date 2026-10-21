"""生成 Tool micro benchmark 的确定性 fixture 目录树。

不涉及 LLM、不涉及 Agent，仅用于测量 Grep / Glob 的搜索性能与正确性。

运行：
    python benchmark/tool_bench/make_fixtures.py

产出：
    benchmark/tool_bench/fixtures/small/    ~25 个文件
    benchmark/tool_bench/fixtures/medium/   ~400 个文件
    benchmark/tool_bench/fixtures/large/    ~6700 个文件（含 node_modules/.git/build 等噪声）
"""

from __future__ import annotations

import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
FIXTURES = BASE / "fixtures"

TARGET = "WENDREAM_SEARCH_TARGET_2026"
ABSENT = "THIS_STRING_ABSENT_98765"

# 这些目录应被默认排除；里面故意放入 TARGET 的诱饵，用于验证排除是否生效
NOISE_DIRS = ["node_modules", ".git", "build", "dist", "__pycache__", ".conda", "venv"]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _filler(index: int) -> str:
    return (
        f"# module {index}\n"
        f"def function_{index}(x):\n"
        f"    return x * {index}\n"
        f"\n"
        f"CONSTANT_{index} = {index}\n"
        f'description = "synthetic filler line {index}"\n'
    )


def build_small() -> None:
    root = FIXTURES / "small"
    for i in range(10):
        _write(root / "src" / f"mod_{i}.py", _filler(i))
    for i in range(5):
        _write(root / "docs" / f"note_{i}.md", f"# note {i}\nTARGET_MARKER = {TARGET}\n")
    for i in range(5):
        _write(root / "data" / f"item_{i}.json", '{"index": %d}\n' % i)
    _write(root / "src" / "target.py", f"# {TARGET}\nfound = True\n")
    _write(root / "README.md", "# small fixture\n")


def build_medium() -> None:
    root = FIXTURES / "medium"
    for d in range(10):
        for f in range(30):
            _write(root / f"pkg_{d}" / "sub" / f"mod_{f}.py", _filler(d * 30 + f))
    for i in range(40):
        _write(root / "docs" / f"doc_{i}.md", f"# doc {i}\nbody line\n")
    for i in range(40):
        _write(root / "data" / f"rec_{i}.json", '{"i": %d}\n' % i)
    _write(root / "pkg_0" / "sub" / "target.py", f"# {TARGET}\n")


def build_large() -> None:
    root = FIXTURES / "large"
    # 主体内容
    for d in range(30):
        for f in range(60):
            _write(root / f"pkg_{d}" / "sub" / f"mod_{f}.py", _filler(d * 60 + f))
    for i in range(200):
        _write(root / "docs" / f"doc_{i}.md", f"# doc {i}\nbody\n")
    for i in range(200):
        _write(root / "data" / f"rec_{i}.json", '{"i": %d}\n' % i)
    _write(root / "src" / "target.py", f"# {TARGET}\nfound = True\n")

    # 噪声目录：数量大 + 内含 TARGET 诱饵（正确实现应默认排除）
    for noise in NOISE_DIRS:
        for d in range(5):
            for f in range(60):
                _write(
                    root / noise / f"vendor_{d}" / f"file_{f}.js",
                    f"// noise {noise} {d} {f}\n" + ("x" * 120) + "\n",
                )
        _write(root / noise / "vendor_0" / "decoy_target.js", f"// {TARGET}\n")


def main() -> None:
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    build_small()
    build_medium()
    build_large()

    for name in ("small", "medium", "large"):
        root = FIXTURES / name
        files = sum(1 for p in root.rglob("*") if p.is_file())
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        print(f"{name}: files={files} bytes={size}")


if __name__ == "__main__":
    main()
