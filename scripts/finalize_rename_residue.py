"""一次性收尾脚本：清掉 rename 后残留的非归属类旧品牌字样。"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EDIT_LISTS: dict[str, list[tuple[str, str]]] = {
    "wendream/app.py": [("_MEWCODE_THEME", "_WENDREAM_THEME")],
    "wendream/skills/install.py": [("mewcode-install-skill", "wendream-install-skill")],
    "wendream/tools/bash.py": [("见 mewcode/sandbox", "见 wendream/sandbox")],
    "wendream/web_content.py": [("与 mewcode/remote.py", "与 wendream/remote.py")],
    "benchmark/adapters/wendream_adapter.py": [
        ("见 mewcode/tools/__init__.py", "见 wendream/tools/__init__.py")
    ],
    "tests/test_branding_version.py": [
        (
            '"""包名仍为 mewcode —— 大规模重构风险过高，本阶段不改。"""',
            '"""包名已迁移为 wendream；不得再引入 mewcode 兼容包。"""',
        )
    ],
    "tests/test_permissions.py": [('"mewcode_test.txt"', '"wendream_test.txt"')],
    "tests/js/xss_sanitizer_check.mjs": [("mewcode/web_content.py", "wendream/web_content.py")],
}


def main() -> None:
    for rel, pairs in EDIT_LISTS.items():
        path = REPO / rel
        original = path.read_text(encoding="utf-8")
        text = original
        for old, new in pairs:
            text = text.replace(old, new)
        if text != original:
            path.write_text(text, encoding="utf-8")
            print("fixed      ", rel)
        else:
            print("unchanged  ", rel)


if __name__ == "__main__":
    main()
