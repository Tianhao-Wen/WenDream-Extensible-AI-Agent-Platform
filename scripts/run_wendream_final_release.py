"""WenDream（rename 后）最终 3-run 验证驱动脚本。

前提：终端环境已设置 DEEPSEEK_API_KEY（本脚本只检查是否存在，绝不打印内容）。

用法：
    python scripts/run_wendream_final_release.py            # 完整流程
    python scripts/run_wendream_final_release.py --check    # 只检查前置条件

流程：
    1) manifest 哈希冻结 -> benchmark/results/final_release_wendream/manifest.json
    2) run1 / run2 / run3：三次全量 30 例，每次前后各记录一次哈希
    3) 用同一分析器汇总 metrics + stability

三次运行之间不改任何代码（脚本会校验前后哈希一致，不一致直接终止）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RELEASE_DIR_NAME = "final_release_wendream"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["WENDREAM_RELEASE_DIR"] = RELEASE_DIR_NAME
    env["PYTHONUTF8"] = "1"
    return env


def _run(args: list[str]) -> int:
    print(f"\n$ {' '.join(args)}", flush=True)
    return subprocess.call(args, cwd=str(REPO), env=_env())


def main() -> int:
    has_key = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    print(f"DEEPSEEK_API_KEY present: {bool(os.environ.get('DEEPSEEK_API_KEY'))}")
    if not has_key:
        print("REGRESSION BLOCKED: DEEPSEEK_API_KEY is not set.", file=sys.stderr)
        return 2
    if "--check" in sys.argv:
        print("preconditions OK")
        return 0

    py = sys.executable
    validator = str(REPO / "benchmark" / "final_release_validate.py")

    if _run([py, validator, "hash"]) != 0:
        return 3
    for name in ("run1", "run2", "run3"):
        if _run([py, validator, "run", name]) != 0:
            return 4
    return _run([py, validator, "metrics", "run1", "run2", "run3", "--official=3"])


if __name__ == "__main__":
    raise SystemExit(main())
