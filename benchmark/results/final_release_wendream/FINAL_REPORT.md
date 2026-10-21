# FINAL REPORT — WenDream（rename 后冻结版本）

> **Public archive note / 公开版本说明：** 本文件为冻结阶段原始报告。文中提及的 raw traces、manifest、hash 等原始材料为保护本机路径/会话隐私未公开，仓库仅保留三轮指标与稳定性汇总。详见同目录 README。

生成时间：2026-10-20
产品身份：**WenDream**（Python 包 / CLI / 运行目录均为 `wendream`）
上游归属：**WenDream is based on and deeply modified from the open-source MewCode Agent project.**

证据目录：`benchmark/results/final_release_wendream/`
（`manifest.json`、`run1|run2|run3/`（含 traces/report/metrics/efficiency/pollution/hashes）、`stability.json`）

---

## 1. Benchmark Design

- **规模**：30 个 case，7 类 —— context 4、file operations 6、tool calling 6、information processing 4、
  vision 4、session 3、error recovery 3。
- **口径**：Task Success Rate = PASSED / (PASSED + FAILED + TIMEOUT)；SKIP 不进分母
  （`VISION_004` provider 不支持 PDF 输入；`SESSION_003` 无稳定自动化接口）。
- **执行**：真实 Agent 循环 + 真实工具执行；每个 case 独立 workspace；`Bash.restrict_to_workspace=True`；
  `case_timeout = 180s`。
- **证据**：每 case 一条 trace（tool_calls 含参数/结果/错误/耗时、agent_messages、permission_decisions、
  session_evidence、timing、counts、evaluation）。
- **冻结项**：cases、evaluator、workspace 语义、prompt、Completion Contract、ToolSearch、lazy exposure、
  `max_iterations` —— 本阶段一行未改。

## 2. Current Product Identity

| 维度 | 值 |
|---|---|
| 产品名 | WenDream |
| Python 包 | `wendream`（`import mewcode` → ModuleNotFoundError） |
| CLI | `wendream`（唯一 console script；`wendream --version` → `WenDream 0.3.0`） |
| 运行时目录 | `.wendream/`（旧 `.mewcode/` 由 `wendream.migration` 一次性迁移） |
| 环境变量前缀 | `WENDREAM_*`（provider 原生 `DEEPSEEK_API_KEY` 等不变） |
| TUI / Remote UI | WenDream |
| 版本源 | `wendream/__init__.py::__version__ = "0.3.0"` |
| 上游归属 | 保留 `MewCode`（ATTRIBUTION / README / pyproject description / `__upstream__`） |

## 3. Test Results

| 范围 | 结果 |
|---|---|
| 安全 / rename 专项（9 文件） | **87 passed** |
| 核心 Agent 测试（16 文件） | **420 passed** |
| 完整套件（37 文件，排除 `test_consolidation.py`） | **786 passed / 2 failed / 1 skipped** |

失败分类：

- `test_hooks.py::TestCommandExecutor::test_timeout` → **KNOWN ENVIRONMENT ISSUE**
  （用例执行 Unix `sleep 10`，Windows 无该命令，拿不到 "timed out"）。
- `test_permissions.py::test_e2e_rule_allows_git` → **KNOWN ENVIRONMENT ISSUE**
  （本仓库不是 git 仓库，`git status` 退出码 128）。
- 无 NEW REGRESSION；`test_hooks` 里原先失败的 `test_pre_tool_use_reject_skips_tool` 现已通过（hook 修复）。

## 4. 3-run Results（`final_release_wendream`）

同一份冻结代码、同一 provider/model，三次连续全量运行，无重跑、无挑选：

| run | PASS | FAIL | TIMEOUT | SKIP | Task Success | wall | sum latency | avg lat | med lat | avg LLM | med LLM | avg Tool | med Tool | post L/T | OV | unnec | TF | U+FFFD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| run1 | 27 | 1 | 0 | 2 | **96.4%** | 304.23s | 303.80s | 10.85s | 6.17s | 4.00 | 4.0 | 2.61 | 3.0 | 27/15 | 9 | 19 | 5/5 | 0 |
| run2 | 28 | 0 | 0 | 2 | **100.0%** | 197.10s | 196.63s | 7.02s | 4.59s | 3.86 | 4.0 | 2.50 | 2.5 | 23/11 | 3 | 17 | 4/5 | 0 |
| run3 | 28 | 0 | 0 | 2 | **100.0%** | 326.87s | 326.36s | 11.66s | 6.55s | 3.79 | 4.0 | 2.46 | 3.0 | 32/19 | 10 | 22 | 5/5 | 0 |

- 唯一失败：run1 的 `ERROR_003`（`checks failed: contains`）—— 与本仓库既往三轮里出现过多次的
  同一 flaky case 一致（模型自写 flaky-tool 脚本的重试语义不稳定）。
- 三轮 `repo pollution`：**0 个 Agent 产生的文件**（`created: []`；仅编排脚本自身写入仓库根的日志被记为
  `modified`，已删除并在下方限制中说明）。

## 5. Median Metrics（对外使用这一组）

| 指标 | median | range（min–max） |
|---|---|---|
| Task Success Rate | **100.0%** | 96.4% – 100.0% |
| wall time | **304.23s** | 197.10 – 326.87s |
| median case latency | **6.17s** | 4.59 – 6.55s |
| avg case latency | 10.85s | 7.02 – 11.66s |
| avg LLM calls | **3.86** | 3.79 – 4.00 |
| median LLM calls | 4.0 | 4.0 – 4.0 |
| avg Tool calls | **2.50** | 2.46 – 2.61 |
| median Tool calls | 3.0 | 2.5 – 3.0 |
| Tool-Free Compliance | **100%**（5/5） | 80% – 100% |
| post-completion LLM / Tool | 27 / 15 | 23–32 / 11–19 |
| OVER_VERIFICATION | 9 | 3 – 10 |
| unnecessary tool calls | 19 | 17 – 22 |
| U+FFFD tool results | **0** | 0 – 0 |
| TIMEOUT | **0** | 0 – 0 |

> 注意：wall 的 spread（197–327s）远大于代码差异可解释的范围，主要来自 provider 延迟波动；
> 因此**必须**以 median + range 呈现，不能只写单轮最好值。

## 6. Stability

- **status flaky：1/30 = 3.3%** —— 仅 `ERROR_003`（FAILED / PASSED / PASSED）。
- **工具路径 flaky：14/30 = 46.7%**（同名 case 三次的工具序列不完全相同，属正常探索差异）。
- LLM 调用极差：平均 0.93，最大 6（`CTX_004`：4 / 9 / 4）。
- Tool 调用极差：平均 0.96，最大 5（`CTX_004`：0 / 5 / 0；即 tool-free 场景在 run2 出现一次 5 次探索）。
- **代码哈希三轮完全一致**：每轮运行前后各做一次快照（`hashes_pre.json` / `hashes_post.json`），
  `code unchanged` 三次全部成立；关键文件与 `manifest.json` 记录一致。

## 7. Old Final vs WenDream Final（同口径对比）

| 指标 | Old Final（rename 前，3 轮 median） | WenDream Final（3 轮 median） |
|---|---|---|
| Task Success Rate | 96.4%（89.3–100） | **100.0%**（96.4–100） |
| TIMEOUT | 0 | **0** |
| wall | 211.85s（170.7–226.0） | 304.23s（197.1–326.9） |
| median latency | 4.71s（4.56–4.96） | 6.17s（4.59–6.55） |
| avg LLM / Tool | 3.75 / 2.43 | 3.86 / 2.50 |
| Tool-Free Compliance | 100%（5/5 ×3） | 100%（中位；run2 为 4/5） |
| post-completion LLM / Tool | 16 / 5 | 27 / 15 |
| OVER_VERIFICATION | 2 | 9 |
| status flaky | 10.0%（3 例） | **3.3%**（1 例） |
| U+FFFD | 0 | 0 |

**结论（不得过度解读）**：

- 任务能力**没有退化**：成功率中位 96.4% → 100%，唯一 flaky case 仍是同一个 `ERROR_003`。
- 效率类指标（wall、latency、post-completion、OV、unnecessary）在这一批运行里**变差**，
  而 wall 的三轮 spread 达 197–327s，**最可能的解释是 provider 侧延迟/生成波动**（同一份代码、
  相同的 prompt hash、相同的工具暴露配置）。本报告**不把 rename 称为性能优化，也不声称性能提升**。
- 真正由本阶段引入的行为变化只有一处：**流式路径现在会执行 `pre_tool_use` hook**（安全修复）。
  benchmark 未配置 hook，因此该修复对上述指标无可测影响。

## 8. Known Limitations

1. **Windows 无 OS 级沙箱**：只有权限规则 + 路径校验，不存在内核级隔离。
2. **editable 安装**：Python introspection（`inspect.getsource` 等）仍可访问仓库源码。
3. **Remote 使用 shared token**，不是完整用户认证系统：无用户体系、无速率限制、无 TLS 终止。
4. **前端依赖 CDN**（marked / highlight.js / DOMPurify）：离线时 markdown 降级为纯文本；未做 SRI。
5. **provider 限制**：PDF 输入不支持（`VISION_004` 恒 SKIP）。
6. **provider 延迟波动**：同代码三轮 wall 197–327s，历史还出现过更长尖峰 → 只能用 median + range。
7. **30 例是项目级工程 benchmark**，不是通用 Agent leaderboard。
8. **`ERROR_003` 长期 flaky**：模型自写"首次失败、重试成功"脚本的重试语义不稳定。
9. **tool-free 行为是概率性的**：三轮中 run2 的 `CTX_004` 仍探索了 5 次（其余两轮为 0）。
10. **历史证据事故**：见 `benchmark/results/final_release/OVERWRITE_INCIDENT.md` ——
    历史 `final_release/run1..3` 的 90 条原始 trace 被本次运行误覆盖（结果目录硬编码 bug，已修复）；
    其派生指标、哈希快照、控制台日志、报告仍完整保留。
11. **编排脚本自身污染**：本次 3-run 的控制台日志被重定向到仓库根（`rel.txt`），
    被污染检查记为 `modified`（非 Agent 行为），事后已删除；后续请把日志写到仓库外。

## 9. Resume-safe Claims

可以写：

- 在 30 例工程 benchmark 上，当前 WenDream 冻结版本 **Task Success 中位 100%（96.4–100），TIMEOUT 0**。
- **U+FFFD 工具输出 0**（Windows 编码修复）。
- **Tool-Free Compliance 中位 100%**（无关场景平均不调用工具）。
- 建立"哈希冻结 + 3 轮重复 + 中位数 + case 级 flaky"的可复现验证流程（三轮代码哈希一致）。
- 安全加固：work_dir 三处统一、非交互 ASK→deny、Remote 默认 loopback + token + Origin、
  Web UI DOMPurify 消毒、统一 `pre_tool_use` hook pipeline（流式路径不再绕过 hook）。

不能写：

- ❌ "性能提升 / 快了多少"——本轮 wall/latency 反而更长，且波动大于代码差异。
- ❌ "100% 稳定通过"——三轮里有一轮 96.4%，status flaky 3.3%。
- ❌ "从零自研 Agent 框架"——基于开源 **MewCode** Agent 框架深度二次开发。
- ❌ "RAG / 向量检索"——项目中不存在 embedding / vector retrieval。
- ❌ 把 rename / hook 修复描述成性能优化。
- ❌ 用单轮最好值（如 197s wall）当最终数字。
