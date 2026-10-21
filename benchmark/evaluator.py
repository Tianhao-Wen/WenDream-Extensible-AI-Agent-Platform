from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def contains(text: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return all(contains(text, item) for item in expected)
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False)
    haystack = str(text).lower()
    needle = str(expected).lower()
    return needle in haystack


def exact_match(actual: Any, expected: Any) -> bool:
    return actual == expected


def numeric_match(actual: Any, expected: Any, tolerance: float = 0.0) -> bool:
    try:
        left = float(actual)
        right = float(expected)
        return abs(left - right) <= tolerance
    except (TypeError, ValueError):
        return False


def json_field(data: Any, field: str, expected: Any) -> bool:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return False
    if not isinstance(data, dict):
        return False
    return data.get(field) == expected


def json_exact(data: Any, expected: Any) -> bool:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return False
    return data == expected


def file_exists(path: str | Path) -> bool:
    return Path(path).exists()


def file_content(path: str | Path | Any, expected: Any) -> bool:
    if isinstance(path, (str, Path)):
        p = Path(path)
        if p.exists():
            content = p.read_text(encoding="utf-8")
            return contains(content, expected)
        if path and isinstance(path, str) and "\n" in path:
            return contains(path, expected)
        return False
    return contains(str(path), expected)


def file_diff(path: str | Path, expected: Any) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    current = p.read_text(encoding="utf-8")
    target = str(expected)
    return current.strip() == target.strip()


def set_match(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual == expected
    left = set(actual) if isinstance(actual, (list, tuple, set)) else {actual}
    right = set(expected) if isinstance(expected, (list, tuple, set)) else {expected}
    return left == right


def ordered_match(actual: Any, expected: Any) -> bool:
    left = list(actual) if isinstance(actual, (list, tuple)) else [actual]
    right = list(expected) if isinstance(expected, (list, tuple)) else [expected]
    return left == right


def command_result(command: str, expected: Any, cwd: str | None = None) -> bool:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        output = result.stdout + result.stderr
        return contains(output, expected)
    except Exception:
        return False


def session_state(actual: Any, expected: Any) -> bool:
    return actual == expected


def structured_fields(actual: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(actual, dict):
        return False
    for key, value in expected.items():
        if key not in actual or actual[key] != value:
            return False
    return True


def composite(text: Any, criteria: list[str] | str) -> bool:
    if isinstance(criteria, str):
        criteria = [criteria]
    for item in criteria:
        if item.startswith("contains:"):
            if not contains(text, item.split(":", 1)[1]):
                return False
        elif item.startswith("equals:"):
            if not exact_match(text, item.split(":", 1)[1]):
                return False
        elif item.startswith("json_field:"):
            field, expected = item.split(":", 2)[1:]
            if not json_field(text, field, expected):
                return False
        else:
            if not contains(text, item):
                return False
    return True


def evaluate_check(
    output: Any,
    check: dict[str, Any],
    workspace: str | Path | None = None,
    context: dict[str, Any] | None = None,
) -> bool:
    check_type = check.get("type") or check.get("kind") or "contains"
    expected = check.get("expected", check.get("value"))
    ctx = context or {}
    if check_type == "contains":
        return contains(output, expected)
    if check_type == "exact_match":
        return exact_match(output, check.get("expected") or check.get("value"))
    if check_type == "numeric_match":
        return numeric_match(output, check.get("expected") or check.get("value"), check.get("tolerance", 0.0))
    if check_type == "json_field":
        return json_field(output, check.get("field"), check.get("expected") or check.get("value"))
    if check_type == "json_exact":
        return json_exact(output, check.get("expected") or check.get("value"))
    if check_type == "file_exists":
        return file_exists(Path(workspace) / str(check.get("path")) if workspace else check.get("path"))
    if check_type == "file_content":
        return file_content(Path(workspace) / str(check.get("path")) if workspace else check.get("path"), check.get("expected") or check.get("value"))
    if check_type == "file_diff":
        return file_diff(Path(workspace) / str(check.get("path")) if workspace else check.get("path"), check.get("expected") or check.get("value"))
    if check_type == "set_match":
        return set_match(output, check.get("expected") or check.get("value"))
    if check_type == "ordered_match":
        return ordered_match(output, check.get("expected") or check.get("value"))
    if check_type == "command_result":
        return command_result(check.get("command"), check.get("expected") or check.get("value"), cwd=str(workspace) if workspace else None)
    if check_type == "session_state":
        return session_state(output, check.get("expected") or check.get("value"))
    if check_type == "structured_fields":
        return structured_fields(output, check.get("expected") or check.get("value"))
    if check_type == "composite":
        return composite(output, expected)
    if check_type == "contains_any":
        items = expected if isinstance(expected, (list, tuple)) else [expected]
        return any(contains(output, item) for item in items)
    if check_type == "contains_all":
        items = expected if isinstance(expected, (list, tuple)) else [expected]
        return all(contains(output, item) for item in items)
    if check_type == "regex":
        # 断言的是**语义**（例如“回答里表达了数量 2”），而不是某个固定措辞。
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        flags = re.IGNORECASE if check.get("ignore_case", True) else 0
        return re.search(str(check.get("pattern", "")), text, flags) is not None
    if check_type == "ordered":
        items = expected if isinstance(expected, (list, tuple)) else [expected]
        return ordered_subsequence(output, items)
    if check_type == "transcript_contains_any":
        items = expected if isinstance(expected, (list, tuple)) else [expected]
        return any(contains(ctx.get("transcript", ""), item) for item in items)
    if check_type == "tool_error_contains":
        for event in ctx.get("tool_events", []):
            if event.get("is_error") and contains(event.get("result", ""), expected):
                return True
        return False
    if check_type == "tool_error_for":
        # 对指定文件的 Read/Write/Edit 调用必须产生 error
        target = str(expected)
        for event in ctx.get("tool_events", []):
            if not event.get("is_error"):
                continue
            args = event.get("arguments") or {}
            blob = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
            if target in blob or target in str(event.get("result", "")):
                return True
        return False
    if check_type == "permission_denied":
        want_tool = check.get("tool_name")
        want_source = check.get("source")
        needle = str(check.get("path_contains") or "")
        for decision in ctx.get("permission_decisions", []):
            if str(decision.get("decision", "")).lower() != "deny":
                continue
            if want_source and decision.get("source") != want_source:
                continue
            if want_tool and decision.get("tool_name") != want_tool:
                continue
            if needle and needle not in str(decision.get("path", "")):
                continue
            return True
        return False
    if check_type == "tool_attempted":
        tools = set(check.get("tools") or [])
        needle = str(check.get("path_contains") or "")
        for event in ctx.get("tool_events", []):
            # 只匹配"调用"事件（有 arguments、没有 result）
            if "result" in event:
                continue
            if tools and event.get("tool_name") not in tools:
                continue
            args = event.get("arguments") or {}
            blob = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
            if needle and needle not in blob:
                continue
            return True
        return False
    if check_type == "file_absent":
        target = Path(workspace) / str(check.get("path")) if workspace else Path(str(check.get("path")))
        return not target.exists()
    if check_type == "file_not_contains":
        target = Path(workspace) / str(check.get("path")) if workspace else Path(str(check.get("path")))
        if not target.exists():
            return True
        return not contains(target.read_text(encoding="utf-8", errors="replace"), expected)
    if check_type == "session_field":
        session = ctx.get("session") or {}
        field = check.get("field")
        return session.get(field) == expected
    raise ValueError(f"Unsupported check type: {check_type}")


def ordered_subsequence(text: Any, terms: list[Any]) -> bool:
    """判断 terms 是否按顺序出现在 text 中（允许中间有其它内容）。"""
    haystack = str(text).lower()
    position = 0
    for term in terms:
        needle = str(term).lower()
        index = haystack.find(needle, position)
        if index < 0:
            return False
        position = index + len(needle)
    return True
