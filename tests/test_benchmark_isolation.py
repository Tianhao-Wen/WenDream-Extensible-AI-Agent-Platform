"""Benchmark 隔离 / 越界防护 / SESSION_002 canary 判定 的回归测试。

三部分：
1. Benchmark 工具集（`restrict_bash_to_workspace=True`）不能让 Agent 读到 repo；
2. PermissionChecker 对 Grep/Glob 检查的是 **path** 而不是 pattern；
3. SESSION_002 的 canary 泄漏判定只看"值"，不看"键名"。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wendream.permissions.checker import PermissionChecker
from wendream.permissions.dangerous import DangerousCommandDetector
from wendream.permissions.modes import PermissionMode
from wendream.permissions.rules import RuleEngine
from wendream.permissions.sandbox import PathSandbox
from wendream.tools import create_default_registry
from wendream.tools.bash import Params as BashParams
from wendream.tools.glob import Glob
from wendream.tools.glob import Params as GlobParams
from wendream.tools.grep import Grep
from wendream.tools.grep import Params as GrepParams
from wendream.tools.read_file import Params as ReadParams

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET_ANSWER = REPO_ROOT / "benchmark" / "cases" / "secret_answer.txt"
SECRET_TEXT = "BENCHMARK_SECRET_DO_NOT_READ"


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    """模拟 Benchmark 的最小结构：workspace + repo 内的答案文件。

    `PathSandbox` 在生产里把系统临时目录当作额外允许根（Agent 需要 /tmp）。
    测试夹具正好位于临时目录下，会让"越界"看起来仍被允许，
    因此这里把该例外指向一个无关目录，才能真实检验边界判定。
    """
    import tempfile as _tempfile

    monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(tmp_path / "synthetic_tmp"))

    ws = tmp_path / "benchmark" / "workspaces" / "CASE_X"
    ws.mkdir(parents=True)
    (ws / "local.txt").write_text("LOCAL_FIXTURE_OK", encoding="utf-8")
    (ws / "allowed").mkdir()
    (ws / "allowed" / "inside.txt").write_text("INSIDE_SEARCHABLE", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE_SECRET", encoding="utf-8")

    # benchmark/cases/secret_answer.txt 是仓库内常驻的 anti-cheating 金丝雀
    assert SECRET_ANSWER.exists(), "缺少 benchmark/cases/secret_answer.txt"
    yield ws, outside


def _bench_registry(ws: Path):
    return create_default_registry(work_dir=str(ws), restrict_bash_to_workspace=True)


def run(tool, params):
    return asyncio.run(tool.execute(params))


# ---------------------------------------------------------------- 1. Benchmark 隔离


def test_benchmark_readfile_cannot_read_repo(bench):
    ws, _ = bench
    tool = _bench_registry(ws).get("ReadFile")
    result = run(tool, ReadParams(file_path=str(SECRET_ANSWER)))
    assert result.is_error
    assert SECRET_TEXT not in result.output


def test_benchmark_readfile_workspace_fixture_is_accessible(bench):
    ws, _ = bench
    tool = _bench_registry(ws).get("ReadFile")
    result = run(tool, ReadParams(file_path="local.txt"))
    assert not result.is_error
    assert "LOCAL_FIXTURE_OK" in result.output


def test_benchmark_grep_cannot_search_repo(bench):
    ws, _ = bench
    tool = _bench_registry(ws).get("Grep")
    result = run(tool, GrepParams(pattern=SECRET_TEXT, path=str(SECRET_ANSWER.parent)))
    assert result.is_error
    assert SECRET_TEXT not in result.output


def test_benchmark_grep_workspace_search_works(bench):
    ws, _ = bench
    tool = _bench_registry(ws).get("Grep")
    result = run(tool, GrepParams(pattern="INSIDE_SEARCHABLE", path="."))
    assert not result.is_error
    assert "allowed/inside.txt" in result.output


def test_benchmark_glob_cannot_scan_repo(bench):
    ws, _ = bench
    tool = _bench_registry(ws).get("Glob")
    result = run(tool, GlobParams(pattern="**/*.json", path=str(REPO_ROOT / "benchmark" / "cases")))
    assert result.is_error


def test_benchmark_glob_workspace_scan_works(bench):
    ws, _ = bench
    tool = _bench_registry(ws).get("Glob")
    result = run(tool, GlobParams(pattern="**/*.txt", path="."))
    assert not result.is_error
    assert "local.txt" in result.output


@pytest.mark.parametrize(
    "command",
    [
        'cd /d "{repo}" && dir /b',
        'cd "{repo}\\benchmark" && type cases\\benchmark_v1_validated.json',
        'type "{answer}"',
        'python -c "print(open(r\'{answer}\').read())"',
        "cd ..\\.. && dir /b",
    ],
)
def test_benchmark_bash_cannot_reach_repo(bench, command):
    ws, _ = bench
    tool = _bench_registry(ws).get("Bash")
    cmd = command.format(repo=REPO_ROOT, answer=SECRET_ANSWER)
    result = run(tool, BashParams(command=cmd))
    assert result.is_error, f"应当拒绝越界命令: {cmd}"
    assert SECRET_TEXT not in result.output


def test_benchmark_bash_workspace_command_works(bench):
    ws, _ = bench
    tool = _bench_registry(ws).get("Bash")
    result = run(tool, BashParams(command='python -c "print(open(\'local.txt\').read())"'))
    assert not result.is_error, result.output
    assert "LOCAL_FIXTURE_OK" in result.output


def test_production_registry_keeps_bash_unrestricted(tmp_path):
    """生产默认必须保持 False：不得把普通用户的 Bash 锁死在 workspace。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    registry = create_default_registry(work_dir=str(ws))
    assert registry.get("Bash").restrict_to_workspace is False
    assert registry.get("Bash").work_dir == str(ws)


# ---------------------------------------------------------------- 2. Permission path


def _checker(ws: Path) -> PermissionChecker:
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(ws)),
        rule_engine=RuleEngine(),
        mode=PermissionMode.DEFAULT,
    )


def test_permission_checks_grep_path_not_pattern(bench):
    ws, outside = bench
    checker = _checker(ws)

    grep = Grep(root=str(ws))
    # 越界 path -> ask
    decision = checker.check(grep, {"pattern": "secret", "path": "../outside"})
    assert decision.effect == "ask"
    assert "路径沙箱" in decision.reason

    # workspace 内 path -> allow
    assert checker.check(grep, {"pattern": "secret", "path": "allowed"}).effect == "allow"
    # 未提供 path -> 视为 "."（workspace 根）-> allow
    assert checker.check(grep, {"pattern": "secret"}).effect == "allow"


def test_permission_does_not_treat_pattern_as_path(bench):
    """`pattern="../foo"` 是搜索表达式，不是路径，不得因此被拦。"""
    ws, _ = bench
    checker = _checker(ws)
    grep = Grep(root=str(ws))
    decision = checker.check(grep, {"pattern": "../foo", "path": "."})
    assert decision.effect == "allow", decision.reason


def test_permission_checks_glob_path(bench):
    ws, outside = bench
    checker = _checker(ws)
    glob_tool = Glob(root=str(ws))
    assert checker.check(glob_tool, {"pattern": "**/*", "path": "../outside"}).effect == "ask"
    assert checker.check(glob_tool, {"pattern": "**/*", "path": "."}).effect == "allow"
    # 绝对越界路径也拦
    absolute = str(outside)
    assert checker.check(glob_tool, {"pattern": "**/*", "path": absolute}).effect == "ask"


def test_permission_still_checks_file_paths(bench):
    ws, _ = bench
    checker = _checker(ws)
    from wendream.tools.write_file import WriteFile

    writer = WriteFile()
    assert checker.check(writer, {"file_path": "ok.txt", "content": "x"}).effect == "allow"
    assert checker.check(writer, {"file_path": "../escape.txt", "content": "x"}).effect == "ask"


# ---------------------------------------------------------------- 3. SESSION_002 canary


from benchmark.session_flows import compute_isolation, detect_canary_leakage  # noqa: E402

CANARY_A = "A-RED-7F3A"
CANARY_B = "B-BLUE-9C2D"


def test_key_name_mention_is_not_leakage():
    """B 自然语言提到键名 A_SECRET，但没有 A 的 canary 值 -> 不算泄漏。"""
    texts_b = "已记住：B_SECRET = B-BLUE-9C2D。如果后续涉及 A_SECRET、C_SECRET 也请告诉我。"
    result = compute_isolation("", texts_b, [CANARY_B], [CANARY_A])
    assert result["leakage_in_b"] == []
    assert result["isolation_ok"] is True


def test_real_canary_in_history_is_leakage():
    """B 的历史里真的出现了 A 的 canary 值 -> 必须判泄漏。"""
    texts_b = f"我记得 A_SECRET = {CANARY_A}，B_SECRET = {CANARY_B}。"
    result = compute_isolation("", texts_b, [CANARY_B], [CANARY_A])
    assert CANARY_A in result["leakage_in_b"]
    assert result["isolation_ok"] is False
    assert result["cross_contamination"]


def test_leakage_detection_is_case_insensitive():
    assert detect_canary_leakage("value=a-red-7f3a", [CANARY_A]) == [CANARY_A]


def test_no_leakage_when_both_sides_clean():
    result = compute_isolation(
        f"A_SECRET = {CANARY_A}", f"B_SECRET = {CANARY_B}", [CANARY_B], [CANARY_A]
    )
    assert result["isolation_ok"] is True
    assert result["cross_contamination"] == []


def test_session002_case_uses_canary_values_not_key_names():
    """用例数据本身：forbid 列表必须只包含值，不得包含键名。"""
    import json

    data = json.loads(
        (REPO_ROOT / "benchmark" / "cases" / "benchmark_v1_validated.json").read_text(
            encoding="utf-8"
        )
    )
    case = next(c for c in data["cases"] if c["id"] == "SESSION_002")
    assert case["expect_a"] == CANARY_A
    assert case["expect_b"] == CANARY_B
    assert case["forbid_in_a"] == [CANARY_B]
    assert case["forbid_in_b"] == [CANARY_A]
    for token in case["forbid_in_a"] + case["forbid_in_b"]:
        assert not token.endswith("_SECRET"), "键名不得作为泄漏证据"


# ---------------------------------------------------------------- 4. TOOL_001 wording


def _tool001_checks():
    import json

    data = json.loads(
        (REPO_ROOT / "benchmark" / "cases" / "benchmark_v1_validated.json").read_text(
            encoding="utf-8"
        )
    )
    case = next(c for c in data["cases"] if c["id"] == "TOOL_001")
    return case["checks"]


@pytest.mark.parametrize(
    "answer",
    [
        "The workspace contains **2 files**, both under `.wendream\\sessions\\`.",
        "There are 2 files.",
        "Total: 2",
        "2 files",
        "The workspace contains two files.",
        "共有 2 个文件",
    ],
)
def test_tool001_accepts_equivalent_wordings(answer):
    from benchmark.evaluator import evaluate_check

    checks = _tool001_checks()
    assert evaluate_check(answer, checks[1]), answer
    # 不再要求出现固定单词 "count"
    assert "count" not in str(checks[1]).lower()


@pytest.mark.parametrize("answer", ["The workspace has 3 files.", "I found some files.", "Done."])
def test_tool001_rejects_wrong_or_vague_count(answer):
    from benchmark.evaluator import evaluate_check

    assert not evaluate_check(answer, _tool001_checks()[1]), answer


def test_tool001_requires_real_tool_use():
    from benchmark.evaluator import evaluate_check

    checks = _tool001_checks()
    tool_check = checks[0]
    no_tools = {"tool_events": []}
    # tool_attempted 只匹配"调用"事件：有 arguments、没有 result
    with_tool = {"tool_events": [{"tool_name": "Glob", "arguments": {"pattern": "**/*"}}]}
    assert not evaluate_check("2 files", tool_check, context=no_tools)
    assert evaluate_check("2 files", tool_check, context=with_tool)
