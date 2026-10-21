"""Phase 2A：Agent 效率诊断（只读分析，不修改 Agent）。

输入：`benchmark/results/v2_phase1c_isolation/full/traces/*.json`
输出：`benchmark/results/v2_phase2a_analysis/`
    - agent_efficiency_baseline.json
    - agent_loop_analysis.md
    - top_llm_cases.csv
    - top_tool_cases.csv
    - tool_call_classification.csv

只使用**可观测事件**：user prompt / tool call / tool result / final answer。
不读取、不推断模型内部推理内容。
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli_paths() -> tuple[Path, Path]:
    """支持 ``analyze_efficiency.py [traces_dir] [out_dir]``。"""
    traces = TRACES
    out = OUT
    if len(sys.argv) > 1:
        traces = Path(sys.argv[1])
    if len(sys.argv) > 2:
        out = Path(sys.argv[2])
    return traces, out


TRACES = REPO_ROOT / "benchmark" / "results" / "v2_phase1c_isolation" / "full" / "traces"
CASES = REPO_ROOT / "benchmark" / "cases" / "benchmark_v1_validated.json"
OUT = REPO_ROOT / "benchmark" / "results" / "v2_phase2a_analysis"

READ_ONLY_TOOLS = {"ReadFile", "Grep", "Glob"}
WRITE_TOOLS = {"WriteFile", "EditFile"}


# --------------------------------------------------------------------------- 基础

def pair_calls(trace: dict) -> list[dict]:
    """把 arguments / result 两条记录按 tool_id 配成有序调用列表。"""
    out: list[dict] = []
    pending: dict[str, dict] = {}
    for tc in trace.get("tool_calls") or []:
        tid = tc.get("tool_id")
        if tc.get("result") is not None or "elapsed" in tc:
            entry = pending.pop(tid, {"tool": tc.get("tool_name"), "args": {}})
            entry["result"] = tc.get("result")
            entry["is_error"] = bool(tc.get("is_error"))
            entry["elapsed"] = tc.get("elapsed")
            out.append(entry)
        else:
            pending[tid] = {"tool": tc.get("tool_name"), "args": tc.get("arguments") or {}}
    for leftover in pending.values():
        leftover["result"] = None
        leftover["is_error"] = False
        leftover["elapsed"] = None
        out.append(leftover)
    return out


def tools_needed(case: dict) -> bool:
    """这个 Case 在逻辑上是否需要访问 workspace。"""
    checks = case.get("checks") or []
    tool_bound = {
        "tool_error_for", "tool_attempted", "command_result", "file_content",
        "file_exists", "file_diff", "file_absent", "file_not_contains", "permission_denied",
    }
    if any(c.get("type") in tool_bound for c in checks):
        return True

    category = str(case.get("category") or "")
    prompt = str(case.get("prompt") or "")
    # 多轮用例：只要任意一轮明确提到文件，就认为需要工具
    texts = [prompt] + [
        str(t.get("prompt") or "") for t in (case.get("history") or []) if isinstance(t, dict)
    ]
    blob = " ".join(texts)
    if re.search(r"\.(txt|md|json|csv|html|py|pdf|png|jpg)\b", blob, re.I):
        return True
    if re.search(r"\b(file|files|path|directory|folder|workspace)\b", blob, re.I):
        return True
    if category in ("Context", "Session"):
        return False
    return True


def norm_path(value: str, workspace: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if workspace:
        ws = workspace.replace("\\", "/").rstrip("/")
        if text.lower().startswith(ws.lower()):
            text = text[len(ws):].lstrip("/")
    return text or "."


def signature(tool: str, args: dict, workspace: str) -> str:
    """用于重复检测的归一化签名。"""
    if tool in ("ReadFile", "WriteFile", "EditFile"):
        return f"{tool}:{norm_path(args.get('file_path', ''), workspace)}"
    if tool == "Grep":
        return f"Grep:{args.get('pattern','')}@{norm_path(args.get('path','.'), workspace)}"
    if tool == "Glob":
        return f"Glob:{args.get('pattern','')}@{norm_path(args.get('path','.'), workspace)}"
    if tool == "Bash":
        cmd = str(args.get("command", ""))
        return "Bash:" + re.sub(r"\s+", " ", cmd).strip().lower()
    return f"{tool}:{json.dumps(args, sort_keys=True, ensure_ascii=True)}"


def primary_key(call: dict) -> str:
    """近似重复检测用的"主参数"（同一主参数、不同范围）。"""
    tool = call["tool"]
    args = call["args"]
    if tool == "Grep":
        return f"Grep:{args.get('pattern','')}"
    if tool == "Glob":
        pat = str(args.get("pattern", "")).replace("**/", "").replace("*", "")
        return f"Glob:{pat}"
    if tool in ("ReadFile", "EditFile", "WriteFile"):
        return f"{tool}"
    if tool == "Bash":
        return "Bash:" + str(args.get("command", ""))[:40]
    return tool


# --------------------------------------------------------------------------- 证据/完成点

def expected_tokens(case: dict) -> list[str]:
    tokens: list[str] = []
    for check in case.get("checks") or []:
        ctype = check.get("type")
        if ctype in ("contains", "contains_any", "contains_all", "ordered", "transcript_contains_any"):
            value = check.get("expected")
            if isinstance(value, list):
                tokens.extend(str(v) for v in value if v)
            elif value:
                tokens.append(str(value))
    return tokens


def find_completion_point(case: dict, calls: list[dict]) -> int | None:
    """第一个"此时 Agent 已具备回答所需证据"的工具调用下标。

    - 任务根本不需要工具：返回 -1（表示 completion 在 0 次工具调用之后）；
    - 有 file_content/file_exists 类检查：第一个成功的写入/编辑即视为产出完成；
    - 其余：第一个同时包含全部 expected token 的工具结果。
    """
    if not tools_needed(case):
        return -1

    checks = case.get("checks") or []
    if any(c.get("type") in ("file_content", "file_diff", "file_not_contains", "file_absent") for c in checks):
        for index, call in enumerate(calls):
            if call["tool"] in WRITE_TOOLS and not call["is_error"]:
                return index
        return None

    tokens = expected_tokens(case)
    if not tokens:
        return None
    needle = [t.lower() for t in tokens]
    for index, call in enumerate(calls):
        blob = str(call.get("result") or "").lower()
        if blob and all(n in blob for n in needle):
            return index
    return None


# --------------------------------------------------------------------------- 分类

def classify_tools(case: dict, calls: list[dict], completion: int | None) -> list[dict]:
    workspace = str(case.get("_workspace") or "")
    seen: dict[str, int] = {}
    seen_primary: dict[str, int] = {}
    rows: list[dict] = []
    prev_error = False

    for index, call in enumerate(calls):
        sig = signature(call["tool"], call["args"], workspace)
        pkey = primary_key(call)
        label = "NECESSARY"
        reason = ""

        if prev_error:
            label, reason = "RECOVERY", "上一步工具失败/被拒后的重试"
        elif not tools_needed(case):
            label, reason = "PREMATURE", "任务不依赖 workspace，工具调用属多余"
        elif sig in seen:
            label, reason = "DUPLICATE", f"与第 {seen[sig] + 1} 次调用完全相同"
        elif pkey in seen_primary and call["tool"] in ("Grep", "Glob"):
            label, reason = "REDUNDANT", f"同一目标换范围重复搜索（第 {seen_primary[pkey] + 1} 次）"
        elif completion is not None and index > completion:
            if call["tool"] in ("Grep", "Glob"):
                label, reason = "OVER_BROAD", "结论已具备后仍进行大范围搜索"
            elif call["tool"] in READ_ONLY_TOOLS or call["tool"] == "Bash":
                label, reason = "VERIFICATION", "已有答案后额外确认"
            else:
                label, reason = "REDUNDANT", "完成后仍在操作"
        elif (call["tool"] == "Glob" and str(call["args"].get("pattern", "")).strip() in ("**/*", "*", "**")) or (
            call["tool"] == "Grep"
            and norm_path(call["args"].get("path", "."), workspace) == "."
            and str(call["args"].get("pattern", "")).strip() in ("", ".")
        ):
            label, reason = "OVER_BROAD", "全工作区无约束搜索"

        seen.setdefault(sig, index)
        seen_primary.setdefault(pkey, index)
        prev_error = call["is_error"]
        rows.append(
            {
                "index": index + 1,
                "tool": call["tool"],
                "args": json.dumps(call["args"], ensure_ascii=True)[:200],
                "is_error": call["is_error"],
                "elapsed": call.get("elapsed"),
                "undecodable": "\ufffd" in str(call.get("result") or ""),
                "classification": label,
                "reason": reason,
            }
        )
    return rows


def classify_llm(case: dict, calls: list[dict], completion: int | None, llm_calls: int) -> dict:
    """LLM 调用分类（基于可观测事件的数量守恒，不猜测内容）。"""
    total = llm_calls
    if completion == -1:
        # 任务不需要任何工具：合理的下限是“规划 + 回答”两次
        necessary = min(total, 2)
        return {
            "INITIAL_PLAN": 1 if total else 0,
            "FINAL_RESPONSE": 1 if total > 1 else 0,
            "POST_TOOL_INTERPRETATION": 0,
            "VERIFICATION": 0,
            "necessary_llm_calls": necessary,
            "avoidable_llm_calls": max(total - necessary, 0),
        }
    if completion is None:
        return {
            "INITIAL_PLAN": 1 if total else 0,
            "POST_TOOL_INTERPRETATION": max(total - 1, 0),
            "FINAL_RESPONSE": 0,
            "UNKNOWN": 0,
            "necessary_llm_calls": total,
            "avoidable_llm_calls": 0,
        }

    post_tool = max(len(calls) - (completion + 1), 0)
    post_llm = post_tool + (1 if post_tool else 0)
    necessary = max(total - post_llm, 1)
    avoidable = total - necessary
    return {
        "INITIAL_PLAN": 1,
        "POST_TOOL_INTERPRETATION": max(necessary - 2, 0),
        "FINAL_RESPONSE": 1,
        "VERIFICATION": post_llm if post_tool else 0,
        "necessary_llm_calls": necessary,
        "avoidable_llm_calls": max(avoidable, 0),
    }


# --------------------------------------------------------------------------- 主流程

def load_cases() -> dict[str, dict]:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["cases"]}


def timeline_markdown(case_id: str, case: dict, trace: dict, calls: list[dict], labels: list[dict]) -> str:
    lines = [f"### {case_id}（{trace.get('category')} · {trace.get('status')}）", ""]
    lines.append(f"- prompt: `{str(case.get('prompt'))[:160]}`")
    lines.append(f"- llm_calls={trace['counts'].get('llm_call_count')} tool_calls={trace['counts'].get('tool_call_count')} "
                 f"permission_requests={trace['counts'].get('permission_request_count')}")
    lines.append("")
    lines.append("```")
    lines.append("User Prompt")
    for row, call in zip(labels, calls):
        arg = json.dumps(call["args"], ensure_ascii=True)[:100]
        status = "ERR" if call["is_error"] else "ok"
        lines.append(f"  -> LLM #{row['index']} -> {call['tool']}({arg})   [{status}] {row['classification']}")
    lines.append("  -> Final Answer")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    traces_dir, out_dir = _cli_paths()
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = load_cases()
    traces = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(traces_dir.glob("*.json"))]

    per_case: list[dict] = []
    all_tool_rows: list[dict] = []

    for trace in traces:
        case = cases.get(trace["case_id"], {})
        calls = pair_calls(trace)
        completion = find_completion_point(case, calls)
        labels = classify_tools(case, calls, completion)
        counts = trace.get("counts") or {}
        llm_calls = int(counts.get("llm_call_count") or 0)
        llm_split = classify_llm(case, calls, completion, llm_calls)

        sigs = [signature(c["tool"], c["args"], str(trace.get("workspace") or "")) for c in calls]
        duplicates = len(sigs) - len(set(sigs))
        post_tool = max(len(calls) - (completion + 1), 0) if completion is not None else 0
        post_llm = max(llm_calls - llm_split["necessary_llm_calls"], 0)
        unnecessary = sum(1 for r in labels if r["classification"] in ("PREMATURE", "OVER_BROAD", "REDUNDANT", "DUPLICATE", "VERIFICATION"))
        over_verification = sum(1 for r in labels if r["classification"] == "VERIFICATION")
        undecodable = sum(1 for r in labels if r["undecodable"])
        counted_tools = int(counts.get("tool_call_count") or 0)

        per_case.append(
            {
                "case_id": trace["case_id"],
                "category": trace.get("category"),
                "status": trace.get("status"),
                "latency_seconds": float(trace.get("timing", {}).get("total_seconds") or 0),
                "llm_calls": llm_calls,
                "tool_calls": counted_tools,
                "tool_calls_captured": len(calls),
                "trace_complete": counted_tools == len(calls),
                "needs_tools": tools_needed(case),
                "permission_requests": int(counts.get("permission_request_count") or 0),
                "completion_point": (completion + 1) if completion is not None and completion >= 0 else (0 if completion == -1 else None),
                "post_completion_tool_calls": post_tool,
                "post_completion_llm_calls": post_llm,
                "duplicate_tool_calls": duplicates,
                "over_verification_count": over_verification,
                "unnecessary_tool_calls": unnecessary,
                "undecodable_tool_results": undecodable,
                **llm_split,
            }
        )

        for row in labels:
            row["case_id"] = trace["case_id"]
            row["category"] = trace.get("category")
            all_tool_rows.append(row)

    # ---------- 排序输出
    def sort_key(row, field):
        return (-row[field], row["case_id"])

    top_llm = sorted(per_case, key=lambda r: sort_key(r, "llm_calls"))[:10]
    top_tool = sorted(per_case, key=lambda r: sort_key(r, "tool_calls"))[:10]

    def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    case_fields = ["case_id", "category", "status", "latency_seconds", "llm_calls", "tool_calls", "permission_requests"]
    write_csv(out_dir / "top_llm_cases.csv", top_llm, case_fields)
    write_csv(out_dir / "top_tool_cases.csv", top_tool, case_fields)
    write_csv(
        out_dir / "tool_call_classification.csv",
        all_tool_rows,
        ["case_id", "category", "index", "tool", "args", "is_error", "undecodable", "elapsed", "classification", "reason"],
    )

    executed = [r for r in per_case if r["status"] in ("PASSED", "FAILED")]
    lat = [r["latency_seconds"] for r in executed if r["latency_seconds"]]
    baseline = {
        "source": str(traces_dir),
        "cases_total": len(per_case),
        "passed": sum(1 for r in per_case if r["status"] == "PASSED"),
        "failed": sum(1 for r in per_case if r["status"] == "FAILED"),
        "timeout": sum(1 for r in per_case if r["status"] == "TIMEOUT"),
        "skipped": sum(1 for r in per_case if r["status"] == "SKIPPED"),
        "task_success_rate": round(
            sum(1 for r in per_case if r["status"] == "PASSED")
            / max(sum(1 for r in per_case if r["status"] in ("PASSED", "FAILED", "TIMEOUT")), 1)
            * 100,
            1,
        ),
        "timeout_rate": round(sum(1 for r in per_case if r["status"] == "TIMEOUT") / max(len(per_case), 1) * 100, 1),
        "avg_llm_calls": round(statistics.mean([r["llm_calls"] for r in executed]), 2),
        "median_llm_calls": statistics.median([r["llm_calls"] for r in executed]),
        "avg_tool_calls": round(statistics.mean([r["tool_calls"] for r in executed]), 2),
        "median_tool_calls": statistics.median([r["tool_calls"] for r in executed]),
        "avg_latency": round(statistics.mean(lat), 2),
        "median_latency": round(statistics.median(lat), 2),
        "total_tool_calls": sum(r["tool_calls"] for r in per_case),
        "duplicate_tool_calls": sum(r["duplicate_tool_calls"] for r in per_case),
        "duplicate_tool_rate": round(
            sum(r["duplicate_tool_calls"] for r in per_case) / max(sum(r["tool_calls"] for r in per_case), 1) * 100, 1
        ),
        "unnecessary_tool_calls": sum(r["unnecessary_tool_calls"] for r in per_case),
        "unnecessary_tool_rate": round(
            sum(r["unnecessary_tool_calls"] for r in per_case) / max(sum(r["tool_calls"] for r in per_case), 1) * 100, 1
        ),
        "over_verification_count": sum(r["over_verification_count"] for r in per_case),
        "post_completion_llm_calls": sum(r["post_completion_llm_calls"] for r in per_case),
        "post_completion_tool_calls": sum(r["post_completion_tool_calls"] for r in per_case),
        "avoidable_llm_calls": sum(r["avoidable_llm_calls"] for r in per_case),
        "cases_gt5_llm": sum(1 for r in executed if r["llm_calls"] > 5),
        "cases_gt5_tool": sum(1 for r in executed if r["tool_calls"] > 5),
        "cases_without_tools": sum(1 for r in executed if r["tool_calls"] == 0),
        "cases_needing_no_tools": [r["case_id"] for r in executed if not r["needs_tools"]],
        "tool_calls_in_tool_free_cases": sum(r["tool_calls"] for r in executed if not r["needs_tools"]),
        "undecodable_tool_results": sum(r["undecodable_tool_results"] for r in per_case),
        "captured_tool_calls": sum(r["tool_calls_captured"] for r in per_case),
        "traces_with_uncaptured_tools": [r["case_id"] for r in per_case if not r["trace_complete"]],
        "tool_call_latency_sum": round(sum(float(r.get("elapsed") or 0) for r in all_tool_rows if r.get("elapsed") is not None), 2),
        "per_case": per_case,
        "classification_totals": {},
    }
    totals: dict[str, int] = {}
    for row in all_tool_rows:
        totals[row["classification"]] = totals.get(row["classification"], 0) + 1
    baseline["classification_totals"] = dict(sorted(totals.items(), key=lambda kv: -kv[1]))

    by_class: dict[str, dict] = {}
    for row in all_tool_rows:
        bucket = by_class.setdefault(row["classification"], {"count": 0, "seconds": 0.0, "cases": set()})
        bucket["count"] += 1
        bucket["seconds"] += float(row.get("elapsed") or 0)
        bucket["cases"].add(row["case_id"])
    baseline["class_breakdown"] = {
        label: {
            "count": data["count"],
            "seconds": round(data["seconds"], 2),
            "cases": sorted(data["cases"]),
        }
        for label, data in sorted(by_class.items(), key=lambda kv: -kv[1]["count"])
    }

    (out_dir / "agent_efficiency_baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # ---------- markdown
    md: list[str] = ["# Agent Loop Analysis", ""]
    md.append(f"数据源：`{traces_dir}`（{len(per_case)} 例）")
    md.append("")
    md.append("## 基线指标")
    md.append("")
    for key in (
        "cases_total", "passed", "failed", "timeout", "skipped", "task_success_rate", "timeout_rate",
        "avg_llm_calls", "median_llm_calls", "avg_tool_calls", "median_tool_calls",
        "avg_latency", "median_latency", "total_tool_calls", "duplicate_tool_calls", "duplicate_tool_rate",
        "unnecessary_tool_calls", "unnecessary_tool_rate", "over_verification_count",
        "post_completion_llm_calls", "post_completion_tool_calls", "avoidable_llm_calls",
        "cases_gt5_llm", "cases_gt5_tool", "cases_without_tools",
    ):
        md.append(f"- **{key}** = {baseline[key]}")
    md.append("")
    md.append("## Top 10（按 LLM calls）")
    md.append("")
    md.append("| case | category | status | latency | llm | tool | perm | completion | post-llm | post-tool | dup | over-verify |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in top_llm:
        md.append(
            f"| {r['case_id']} | {r['category']} | {r['status']} | {r['latency_seconds']:.2f} | {r['llm_calls']} | "
            f"{r['tool_calls']} | {r['permission_requests']} | {r['completion_point']} | {r['post_completion_llm_calls']} | "
            f"{r['post_completion_tool_calls']} | {r['duplicate_tool_calls']} | {r['over_verification_count']} |"
        )
    md.append("")
    md.append("## Top 10（按 Tool calls）")
    md.append("")
    md.append("| case | category | status | latency | llm | tool | perm | completion | post-llm | post-tool | dup | over-verify |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in top_tool:
        md.append(
            f"| {r['case_id']} | {r['category']} | {r['status']} | {r['latency_seconds']:.2f} | {r['llm_calls']} | "
            f"{r['tool_calls']} | {r['permission_requests']} | {r['completion_point']} | {r['post_completion_llm_calls']} | "
            f"{r['post_completion_tool_calls']} | {r['duplicate_tool_calls']} | {r['over_verification_count']} |"
        )
    md.append("")
    md.append("## 工具调用分类合计")
    md.append("")
    for label, count in baseline["classification_totals"].items():
        md.append(f"- {label}: {count}")
    md.append("")
    md.append("## 可观测执行序列（Top cases）")
    md.append("")
    md.append("只记录 user prompt → LLM#n → tool → tool result → ... → final answer，不包含任何模型内部推理。")
    md.append("")
    for r in top_llm[:6]:
        trace = next(t for t in traces if t["case_id"] == r["case_id"])
        case = cases.get(r["case_id"], {})
        calls = pair_calls(trace)
        labels = classify_tools(case, calls, find_completion_point(case, calls))
        md.append(timeline_markdown(r["case_id"], case, trace, calls, labels).replace("\n", "\n"))
    (out_dir / "agent_loop_analysis.md").write_text("\n".join(md), encoding="utf-8")

    print(f"cases analyzed: {len(per_case)}")
    for key in ("avg_llm_calls", "avg_tool_calls", "duplicate_tool_rate", "unnecessary_tool_rate",
                "post_completion_llm_calls", "post_completion_tool_calls", "cases_gt5_llm", "cases_gt5_tool",
                "undecodable_tool_results", "captured_tool_calls"):
        print(f"  {key} = {baseline[key]}")
    print("classification:", baseline["classification_totals"])
    print(f"written -> {out_dir}")


if __name__ == "__main__":
    main()
