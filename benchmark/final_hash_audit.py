"""为 run1–run3 生成哈希校验记录（追溯校验：与 manifest 比对 + 目录时间顺序）。

run4_audit 已由 `final_release_validate.py run` 在运行时前后各记录一次哈希，
无需追溯校验，本脚本也会把它们汇总进 verification_summary.json。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.final_release_validate import (  # noqa: E402
    CASES_PATH,
    MANIFEST,
    file_hashes,
    prompt_info,
    sha256,
)

RELEASE_DIR = REPO_ROOT / "benchmark" / "results" / "final_release"


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    now = datetime.now().isoformat(timespec="seconds")
    current = file_hashes()
    prompt_sha = prompt_info()["sha256"]
    cases_sha = sha256(CASES_PATH)

    manifest_matches = all(
        manifest["key_file_sha256"].get(rel) == current[rel] for rel in current
    ) and manifest["system_prompt"]["sha256"] == prompt_sha and (
        manifest["benchmark"]["cases_sha256"] == cases_sha
    )

    summary = {
        "checked_at": now,
        "manifest_timestamp": manifest["timestamp"],
        "manifest_matches_current_code": manifest_matches,
        "system_prompt_sha256": prompt_sha,
        "cases_sha256": cases_sha,
        "runs": {},
    }

    for name in ("run1", "run2", "run3", "run4_audit"):
        run_dir = RELEASE_DIR / name
        if not run_dir.exists():
            continue
        pre_path = next(
            (p for p in (run_dir / "hashes_pre.json", run_dir / "hashes_before.json") if p.exists()),
            None,
        )
        post_path = next(
            (p for p in (run_dir / "hashes_post.json", run_dir / "hashes_after.json") if p.exists()),
            None,
        )
        record = {
            "run_dir": str(run_dir.relative_to(REPO_ROOT)),
            "run_dir_created": datetime.fromtimestamp(
                run_dir.stat().st_ctime
            ).isoformat(timespec="seconds"),
            "manifest_created": manifest["timestamp"],
            "method": "runtime-pre/post-snapshot" if pre_path else "retrospective-vs-manifest",
            "runtime_snapshot_files": [str(p.name) for p in (pre_path, post_path) if p],
            "key_file_sha256": current,
            "system_prompt_sha256": prompt_sha,
            "cases_sha256": cases_sha,
            "identical_to_manifest": manifest_matches,
            "traces": len(list((run_dir / "traces").glob("*.json"))),
            "models": {},
        }
        models: dict[str, int] = {}
        for f in sorted((run_dir / "traces").glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            for c in data if isinstance(data, list) else [data]:
                if isinstance(c, dict):
                    key = f"{c.get('provider')}/{c.get('model')}"
                    models[key] = models.get(key, 0) + 1
        record["models"] = models
        if pre_path and post_path:
            pre = json.loads(pre_path.read_text(encoding="utf-8"))
            post = json.loads(post_path.read_text(encoding="utf-8"))
            if "key_file_sha256" in pre and "key_file_sha256" in post:
                unchanged = (
                    pre["key_file_sha256"] == post["key_file_sha256"]
                    and pre.get("system_prompt_sha256") == post.get("system_prompt_sha256")
                    and pre.get("cases_sha256") == post.get("cases_sha256")
                )
            else:
                unchanged = pre == post
            consistency_path = run_dir / "hash_consistency.json"
            record["pre_post_identical"] = unchanged and (
                not consistency_path.exists()
                or json.loads(consistency_path.read_text(encoding="utf-8")).get("identical") is True
            )
            record["pre_matches_current"] = all(
                pre.get(rel) == current[rel] for rel in current if rel in pre
            ) and pre.get("__system_prompt__", prompt_sha) == prompt_sha
        (run_dir / "verification.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary["runs"][name] = {
            "method": record["method"],
            "identical_to_manifest": record["identical_to_manifest"],
            "pre_post_identical": record.get("pre_post_identical"),
            "pre_matches_current": record.get("pre_matches_current"),
            "models": models,
            "traces": record["traces"],
        }

    (RELEASE_DIR / "verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
