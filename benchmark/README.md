# WenDream Benchmark

本目录包括 WenDream 的本地 Agent 端到端评测框架。评测场景使用本地 Fixture，但 **模型推理仍需连接 LLM Provider**，不是完全离线运行。

## 推荐评测入口

使用 `runner_validated.py`，它按用例建立独立 Workspace，通过真实 Agent/Tool 调用、结构化断言和运行 Trace 验证结果。

```bash
# 在仓库根目录，已安装依赖并配置好 .wendream/config.yaml 后
python -m benchmark.runner_validated --case-id CTX_001 --results-dir local_check

# 运行全套 30 项（涉及真实模型 API 成本）
python -m benchmark.runner_validated --results-dir local_full --case-timeout 180
```

`benchmark/runner.py` 与 `benchmark/cases/benchmark_v1.json` 是早期/兼容路径，**不代表发布版任务成功率**。正式冻结评测采用 `runner_validated.py` 和 `cases/benchmark_v1_validated.json`。

## 冻结版本结果（2026-10-20）

共 7 类 30 项任务。两项 SKIP（PDF Provider 能力及 Edit/Regenerate 自动化入口），每轮 **28 项可评分**：

| 轮次 | PASS | FAIL | TIMEOUT | SKIP | Task Success |
| --- | ---: | ---: | ---: | ---: | ---: |
| Run 1 | 27 | 1 | 0 | 2 | 96.4% |
| Run 2 | 28 | 0 | 0 | 2 | 100% |
| Run 3 | 28 | 0 | 0 | 2 | 100% |

三轮合计 **83/84 项可评分执行通过，0 次 TIMEOUT**。这是一组固定内部工程场景的结果，不是公开排行榜或生产服务 SLA。报告详见 [`results/final_release_wendream/`](results/final_release_wendream/README.md)。

## 证据发布范围

公开仓库只附上汇总报告、三轮 metrics 和稳定性统计。原始 Tool Trace、会话消息、绝对本地文件路径及运行工作区由于可能涉及隐私，被**有意排除**。`benchmark/tool_bench/fixtures` 为脚本生成物，可运行 `python benchmark/tool_bench/make_fixtures.py` 重建。搜索性能对照见 `benchmark/tool_bench/results/`。
