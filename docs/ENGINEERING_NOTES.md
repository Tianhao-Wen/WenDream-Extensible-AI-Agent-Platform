# Engineering notes / 工程问题与优化记录

WenDream 以开源 MewCode Agent 为基础。以下工程问题与迭代成果来自项目自建 Benchmark、专项工具测试和历史报告；它们不代表通用 Agent 排名。

## 1. 减少冗余工具调用

- 症状：任务已完成仍重复探索文件，简单问答不必要地调用 Bash/Glob。
- 分析：检查真实执行 Trace、后续 LLM/Tool 调用、过度验证等指标。
- 改进：Completion Contract、Conversation-first、ToolSearch/Lazy Tool Exposure 与运行时工具优化。
- 观察：跨多阶段版本，平均 Tool 调用 **6.07 → 2.50**（约 -59%），平均 LLM 调用 **5.71 → 3.86**（约 -32%）。变化包含工具/基础设施修复，不能将全部收益归因于单一提示词策略。

## 2. Grep 搜索边界

- 修复 Workspace 根路径不一致、排除目录及 Windows junction 等问题。
- 独立无匹配 Grep 性能场景：**43.068s → 0.937s**（-97.8%）。
- 这是大型合成 Fixture 的特定场景，不等于所有搜索都提升 97.8%。
- 可复现脚本：`python benchmark/tool_bench/make_fixtures.py` 后运行 `python benchmark/tool_bench/run_bench.py --label manual`。

## 3. Windows 与安全

- Shell 非零退出码、Windows 编码及超时处理。
- 修复非交互权限确认路径、统一 Hook Pipeline、Remote 鉴权、Origin 和 XSS 过滤。
- OS 级沙箱平台限制：Linux 可使用 bubblewrap，macOS 可使用 Seatbelt；Windows 无等价 OS 级隔离。

## 4. 三轮评测

2026-10-20 发布版三轮 27/28、28/28、28/28 可评分任务 PASS，0 超时。2 SKIP/轮，`ERROR_003` 在一轮出现失败。原始 Tool Trace 不公开，避免对话及本机路径泄露。
