"""一次性品牌迁移脚本：MewCode -> WenDream。

只处理 ACTIVE 代码与文档；**绝不触碰**：
  * benchmark/results/**（历史证据）
  * benchmark/workspaces/**（运行时残留）
  * 本文件自身（保留旧品牌字符串作为迁移参考）

可重复执行：已迁移的内容不会被二次修改。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 明确排除：历史证据 / 运行时残留 / 本脚本
EXCLUDE_PREFIXES = (
    "benchmark/results/",
    "benchmark/workspaces/",
    "benchmark/cases/",
    "benchmark/fixtures/",
    "benchmark/tool_bench/fixtures/",
    ".conda/",
    ".pytest_cache/",
)
EXCLUDE_FILES = {
    "scripts/migrate_to_wendream.py",
    "build_rename_inventory.py",
    "WENDREAM_RENAME_INVENTORY.md",
    "WENDREAM_RENAME_AUDIT.md",
    "FINAL_CODE_AUDIT_FIX_REPORT.md",  # 历史审计报告，保持当时事实
    "uv.lock",  # 需要由包管理器重新生成，不能文本改写
    ".mewcode/config.yaml",  # 本地凭据，单独处理
}

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".pyc",
    ".so", ".dll", ".exe", ".db", ".sqlite", ".woff", ".woff2", ".ttf",
}

# 迁移后仍允许保留 MewCode 的文件（上游归属）
ATTRIBUTION_FILES = {
    "ATTRIBUTION.md",
    "docs/ATTRIBUTION.md",
    "README.md",
    "pyproject.toml",
    "wendream/__init__.py",
    "wendream/__main__.py",
}

RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("import-from", re.compile(r"\bfrom mewcode\b"), "from wendream"),
    ("import-module", re.compile(r"\bimport mewcode\b"), "import wendream"),
    ("module-attr", re.compile(r"\bmewcode\."), "wendream."),
    ("path-literal", re.compile(r'(["\'])mewcode/'), r"\1wendream/"),
    ("env-prefix", re.compile(r"\bMEWCODE_"), "WENDREAM_"),
    ("app-class", re.compile(r"\bMewCodeApp\b"), "WenDreamApp"),
    ("theme-name", re.compile(r'"mewcode"'), '"wendream"'),
    ("localstorage", re.compile(r"\bmewcode_(sidebar|theme|token)"), r"wendream_\1"),
    ("runtime-dir", re.compile(r"\.mewcode\b"), ".wendream"),
    ("display-name", re.compile(r"\bMewCode\b"), "WenDream"),
]

SKIP_RULE_FOR_FILE = {
    # 归属文件里不允许把 MewCode 改成 WenDream（例：description / attribution）
    ("display-name", "ATTRIBUTION.md"),
    ("display-name", "docs/ATTRIBUTION.md"),
}


def iter_targets() -> list[Path]:
    out: list[Path] = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO).as_posix()
        if any(rel.startswith(p) for p in EXCLUDE_PREFIXES):
            continue
        if rel in EXCLUDE_FILES:
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        if "__pycache__" in path.parts:
            continue
        if path.stat().st_size > 4 * 1024 * 1024:
            continue
        out.append(path)
    return out


def move_package() -> bool:
    src = REPO / "mewcode"
    dst = REPO / "wendream"
    if not src.exists():
        print("package: already migrated (mewcode/ absent)")
        return False
    if dst.exists():
        raise SystemExit("wendream/ already exists but mewcode/ still present — resolve manually")
    # 先清掉旧包里的 __pycache__，避免搬到新目录里的陈旧字节码
    for cache in src.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    shutil.move(str(src), str(dst))
    print("package: moved mewcode/ -> wendream/")
    return True


def rewrite_text(text: str, rel: str, counters: dict[str, int]) -> str:
    for name, pattern, repl in RULES:
        if (name, rel) in SKIP_RULE_FOR_FILE:
            continue
        if name == "display-name" and rel in ATTRIBUTION_FILES:
            # 这些文件里的 MewCode 可能出现在 attribution 语境，逐行处理
            new_lines = []
            for line in text.splitlines(keepends=True):
                lower = line.lower()
                if any(k in lower for k in ("based on", "upstream", "mewcode agent", "attribution", "framework")):
                    new_lines.append(line)
                else:
                    new_text, n = pattern.subn(repl, line)
                    counters[name] += n
                    new_lines.append(new_text)
            text = "".join(new_lines)
            continue
        text, n = pattern.subn(repl, text)
        counters[name] += n
    return text


def main() -> None:
    move_package()
    counters: dict[str, int] = {name: 0 for name, _, _ in RULES}
    changed_files = 0
    for path in iter_targets():
        rel = path.relative_to(REPO).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        new_text = rewrite_text(text, rel, counters)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed_files += 1

    print(f"rewrote {changed_files} files")
    for name, count in counters.items():
        if count:
            print(f"  {name:14s} {count}")


if __name__ == "__main__":
    sys.exit(main())
