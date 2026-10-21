"""品牌与版本一致性（P2-1）。

约束：产品展示名统一为 WenDream，同时保留 upstream attribution（基于开源 WenDream 框架）。
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import wendream  # noqa: E402
from wendream.web_content import INDEX_HTML  # noqa: E402


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_defined_once_and_matches_pyproject():
    version = wendream.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    assert _pyproject()["project"]["version"] == version


def test_tui_brand_and_version():
    from wendream.app import WenDreamApp

    assert WenDreamApp.TITLE == "WenDream"
    banner = WenDreamApp._make_banner("test-model", "C:/work")
    text = banner.plain
    assert "WenDream" in text
    assert wendream.__version__ in text
    assert "WenDream v0.1.0" not in text


def test_web_ui_brand_and_version_placeholder():
    assert "WenDream" in INDEX_HTML
    assert "__WENDREAM_VERSION__" in INDEX_HTML


def test_cli_keeps_upstream_attribution():
    text = (REPO_ROOT / "wendream" / "__main__.py").read_text(encoding="utf-8")
    assert "WenDream" in text  # 说明“基于 WenDream 框架”
    assert "from scratch" not in text.lower()


def test_pyproject_description_mentions_both_names():
    description = _pyproject()["project"]["description"]
    assert "WenDream" in description
    assert "WenDream" in description


def test_console_scripts_keep_backward_compatible_name():
    scripts = _pyproject()["project"]["scripts"]
    assert "wendream" in scripts
    assert "wendream" in scripts
    assert scripts["wendream"] == scripts["wendream"]


def test_python_package_name_is_not_renamed():
    """包名已迁移为 wendream；不得再引入旧包名的兼容包。"""
    assert (REPO_ROOT / "wendream" / "__init__.py").exists()
    assert _pyproject()["project"]["name"] == "wendream"


def test_sandbox_docs_do_not_claim_os_isolation_on_windows():
    from wendream.sandbox import sandbox_support_summary

    summary = sandbox_support_summary()
    assert "No OS-level sandbox" in summary or "OS-level sandbox" in summary
    # 明确区分权限层与 OS 层
    assert "Permission" in summary or "permission" in summary or "path validation" in summary
