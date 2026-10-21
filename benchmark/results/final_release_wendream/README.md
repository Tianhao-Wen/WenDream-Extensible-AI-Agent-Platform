# Final Release — Published Summary

2026-10-20 冻结的 WenDream v0.3.0，使用相同代码和模型配置连续运行 3 轮，保留每轮的 `metrics.json` 与总体 `stability.json`。

- 30 个 Case：27/1/0/2、28/0/0/2、28/0/0/2（顺序为 PASS / FAIL / TIMEOUT / SKIP）。
- 28 个可评分 Case/轮，累计通过 83/84；任务成功率范围 96.4%–100%，没有 TIMEOUT。
- 三轮指标中位：平均工具调用 2.50 次/任务、LLM 调用 3.86 次/任务、单例延迟中位数 6.17 秒。
- 仍存在一个跨轮状态不稳定 Case `ERROR_003`；最终版不能称为持续 100% 成功。
- 两项 SKIP：`VISION_004`（提供者 PDF 输入不支持），`SESSION_003`（无稳定的 Edit/Regenerate 自动化入口）。

原始报告 [`FINAL_REPORT.md`](FINAL_REPORT.md) 为当时本地实验记录，文中提到的 raw traces / manifest / hash 文件 **在此公开版本中不提供**；它们仅在实验原始归档中保留，不应用公开仓库的缺失文件验证原始结果。发布版本没有二次执行付费 Provider Benchmark。
