<div align="center">

# 🤖 WenDream｜可扩展 AI Agent 平台

**面向自然语言任务、文件操作、代码检索及多步骤工具执行的智能体应用**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Agent](https://img.shields.io/badge/Architecture-LLM%20Agent-purple?style=flat-square)](#-系统架构)
[![MCP](https://img.shields.io/badge/Integration-MCP-orange?style=flat-square)](#-mcp-与工具扩展)
[![UI](https://img.shields.io/badge/Interface-TUI%20%2B%20Web-green?style=flat-square)](#-web-与终端交互)
[![Benchmark](https://img.shields.io/badge/Benchmark-30%20Cases-informational?style=flat-square)](#-benchmark-评测结果)
[![Timeout](https://img.shields.io/badge/Frozen%20Runs-0%20Timeout-brightgreen?style=flat-square)](#-benchmark-评测结果)

*流式工具调用 · 上下文管理 · MCP 扩展 · Multi-Agent · Web UI · 自动化评测*

[English](README.md) · **简体中文**

</div>

---

## 🎬 Web 操作演示

> **演示视频准备中。** 计划录制真实 WenDream 浏览器页面，从用户输入到模型回复和工具执行的完整过程。视频录制完成后，会在这里添加封面和播放链接。

计划展示：

1. 启动 Web UI，新建对话并查看流式回答；
2. 用自然语言要求 Agent 读取并总结一个示例文件；
3. 展示工具执行状态、输出结果与异常反馈；
4. 根据上一轮上下文继续提问，并完成一次安全的文件写入；
5. 切换到已有 Session，演示会话恢复。

<!--
录好视频后：
1. 把实际视频放在 assets/wendream_web_demo.mp4，封面截图放在 assets/demo_thumbnail.png；
2. 删除上面的「演示视频准备中」，取消下面两行的注释：

[![WenDream Web Demo](./assets/demo_thumbnail.png)](./assets/wendream_web_demo.mp4?raw=1)

[▶ 观看 WenDream Web 完整演示](./assets/wendream_web_demo.mp4?raw=1)

发布前检查视频中没有 API Key、真实个人路径或隐私对话。
-->

---

## ✨ 核心功能

| 功能 | 说明 |
|---|---|
| 🧠 **LLM Agent** | 根据用户目标进行模型推理、工具决策和多步骤任务执行 |
| ⚡ **流式 Tool Calling** | 逐步输出模型内容，对符合条件的工具调用支持异步提交执行 |
| 🛠️ **文件与 Shell 工具** | 支持 ReadFile、WriteFile、EditFile、Grep、Glob 和 Bash |
| 🔎 **ToolSearch** | 对探索型工具采用按需暴露策略，减少无必要的工具探索 |
| 🔌 **MCP 扩展** | 支持通过 Stdio、HTTP 连接外部 MCP 服务 |
| 🧩 **上下文压缩** | 超长工具结果落盘、历史对话摘要及近期上下文保留 |
| 💾 **Session / Memory** | 会话持久化、恢复及项目相关记忆 |
| 👥 **Multi-Agent** | 子 Agent、任务协作及可选 Git Worktree 工作区隔离 |
| 🖥️ **TUI / Web UI** | 提供 Textual 终端界面和 WebSocket 浏览器界面 |
| 🛡️ **权限与安全** | 工具权限检查、Hook 拦截、远程访问控制 |
| 🧪 **Agent Benchmark** | 7 类 30 项端到端工程测试及重复运行统计 |

---

## 🏗️ 系统架构

```text
                  ┌─────────────────────────────┐
                  │           用户请求           |
                  │       TUI / Web UI          │
                  └──────────────┬──────────────┘
                                 │
                                 ▼
                  ┌─────────────────────────────┐
                  │        WenDream Agent       │
                  │  上下文 / 工具决策 / 任务完成判断  │
                  └──────────┬─────────┬────────┘
                             │         │
                      模型推理│         │工具调用
                             ▼         ▼
                  ┌──────────────┐ ┌──────────────────────┐
                  │ LLM Provider │ │ Tool Registry/Search │
                  │ Anthropic    │ │ 文件 / Grep / Glob   │
                  │ OpenAI       │ │ Bash / MCP / Skills  │
                  │ 兼容 API      │ │ Subagents            │
                  └──────────────┘ └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │ 权限检查 / Hook / 工作区 │
                                   └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │    实际工具执行结果    │
                                   └──────────┬───────────┘
                                              │
                  Agent 继续处理结果 ◄───────────┘
                            │
                            ▼
                     流式生成最终回答

       Session / Memory / Context      Benchmark / Trace / 回归测试
```

### 典型任务流程

```text
用户输入
  ↓
加载上下文和可用工具定义
  ↓
LLM 生成回答或 Tool Call
  ↓
权限及 Hook 检查
  ↓
执行允许的工具，收集结果或错误
  ↓
更新上下文并判断任务是否完成
  ↓
通过 TUI / Web UI 输出结果
```

对于简单问答，Agent 不需要强行调用工具；需要实际操作时，再根据模型决策和权限规则执行相应工具。

---

## ⚡ Agent 工具调用

系统包含异步 Agent Loop 和 `StreamingExecutor`，支持模型流式输出、工具调用收集及符合条件的工具异步执行。工具执行仍受具体实现、权限和副作用约束，并不是所有读写操作都无条件并发。

工程调优主要围绕以下问题：

- **Conversation-first：** 简单问题优先直接回答，减少无意义地检查工作目录；
- **Completion Contract：** 明确任务完成条件，抑制任务已完成后继续验证；
- **ToolSearch / Lazy Tool Exposure：** 探索型工具默认不全部暴露，需要时再加载；
- **Trace 分析：** 记录工具调用、错误、耗时和执行结果，用数据定位低效行为。

---

## 🧩 上下文压缩与会话管理

WenDream 支持两层上下文处理：

**L1：超长工具结果落盘。** 当单条工具结果超过 **50,000 字符**时，可将完整内容保存到磁盘，在模型上下文中保留约 **2,000 字符**的预览和引用信息，避免长输出重复挤占上下文。

**L2：历史对话压缩。** 当上下文接近限制时，对较早的对话进行摘要，并保留近期原文。当前代码中的核心参数如下：

| 参数 | 当前设置 |
|---|---:|
| 单条工具结果处理阈值 | 50,000 字符 |
| 结果预览长度 | 2,000 字符 |
| 近期上下文保留目标 | 10,000 tokens |
| 自动压缩软触发安全边距 | 13,000 tokens |
| 硬触发安全边距 | 3,000 tokens |

Session 数据支持持久化和恢复。需要说明的是，**历史摘要不是无损备份**，不能保证所有被压缩的旧内容逐字保留。

---

## 🔌 MCP 与工具扩展

| 工具能力 | 用途 |
|---|---|
| `ReadFile` | 读取源码与文档 |
| `WriteFile` / `EditFile` | 创建及修改文件 |
| `Grep` / `Glob` | 查找文本内容或匹配文件 |
| `Bash` | 执行授权的本地命令 |
| `ToolSearch` | 按需查找未直接暴露的工具 |
| MCP | 调用独立配置的外部 MCP 服务 |
| Skills / Subagents | 扩展任务处理与子任务协作 |

默认工具注册表中，常见的读写及 Grep 工具保持直接可用；Bash、Glob 等探索型工具可通过 ToolSearch 按需暴露。这样既减少不必要的工具定义，又避免普通文件任务每次都先执行一次 ToolSearch。

MCP 服务需要用户自行配置，仓库不附带已经登录的第三方账号。

---

## 👥 多 Agent 与 Worktree

WenDream 包含 Subagent、Coordinator、Team Mailbox 和 Git Worktree 等能力，可用于拆分不同子任务、共享任务状态，以及为适合并行的操作分配独立工作目录。这些均为**可选功能**，日常对话和普通文件操作可以在单 Agent 模式下完成。

```text
                     Main Agent
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          Explore       Plan       General
             │           │           │
             └───────────┼───────────┘
                         ▼
                   汇总任务结果
```

---

## 🖥️ Web 与终端交互

浏览器界面提供自然语言对话、流式回答、工具执行反馈、会话浏览及支持的文件/图片附件输入。PDF 等内容能否被模型理解，取决于实际 Provider 能力。

**Web 模式：**

```bash
wendream --remote
```

在浏览器中访问 **http://127.0.0.1:18888**。

**终端模式：**

```bash
wendream
```

**非交互模式：**

```bash
wendream -p "概括当前项目的目录结构"
```

非交互模式下，需要用户确认的 `ASK` 权限默认拒绝；不要在不可信目录下使用绕过权限的模式。

---

## 🔐 权限与安全机制

项目包含权限模式、规则检查、Hook 拦截、Workspace 路径处理以及 Remote Token/Origin 等访问限制。

- 工具运行前执行权限检查，支持阻止受限操作；
- 流式 Agent 执行链路包含 `pre_tool_use` Hook；
- Remote 默认只监听本机回环地址，非本机绑定需要 Token（除非主动启用危险覆盖选项）；
- Web 内容展示包含 HTML 安全过滤；
- Linux/macOS 可分别依赖支持环境中的 bubblewrap / Seatbelt；**Windows 当前没有等价的 OS 级内核沙箱**。

> 工作目录限制和权限检查不等于完整安全隔离，尤其在允许 Shell 执行时，应谨慎处理不可信命令和敏感文件。

---

## 🚀 快速开始

### 运行环境

- Python **3.11+**；
- 可用的大模型 API 及其凭据；
- 使用 Web 界面时需要浏览器；
- Git Worktree 等可选能力需要额外系统环境支持。

### 1. 获取源码并安装

```bash
git clone <YOUR-WENDREAM-REPOSITORY-URL>
cd WenDream
python -m pip install -e .
```

### 2. 配置模型

仓库提供无密钥模板 `config.example.yaml`。

macOS / Linux：

```bash
mkdir -p .wendream
cp config.example.yaml .wendream/config.yaml
read -rsp 'DEEPSEEK_API_KEY: ' DEEPSEEK_API_KEY; echo
export DEEPSEEK_API_KEY
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force .wendream | Out-Null
Copy-Item config.example.yaml .wendream/config.yaml
$secureKey = Read-Host "DEEPSEEK_API_KEY" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
```

默认模板使用 DeepSeek 的 OpenAI-Compatible API；支持自行修改 Provider、协议与模型名称。真实 Key 不要填写进 Git 仓库。

### 3. 启动程序

```bash
wendream --version
wendream                 # 终端 TUI
wendream --remote        # 浏览器 Web UI
```

注意：模型调用仍需访问对应 Provider，可能产生 API 费用。

---

## 🎥 建议录制的 Web 演示流程

准备一个不含个人信息的测试文件 `demo_notes.md`，然后按下面的顺序录制：

| 操作 | 示例指令 | 适合展示的内容 |
|---|---|---|
| 普通问答 | “用三点介绍 AI Agent 能完成的任务。” | 流式回答及无需工具的场景 |
| 读取文件 | “读取 `demo_notes.md` 并整理核心需求。” | 工具调用及文件总结 |
| 多轮对话 | “根据上面的需求，给我列一份行动清单。” | 上下文延续 |
| 写入文件 | “把行动清单保存为 `demo_tasks.md`。” | 实际文件操作及执行反馈 |
| 会话恢复 | 返回历史会话，继续之前的任务。 | Session 管理和 Web 交互 |

视频不要拍到 API Key、个人文件路径、隐私历史记录。最好保留真实的工具执行过程，而不是仅展示完成后的静态聊天截图。

---

## 📊 工程优化结果

通过 Trace、专项性能测试及多阶段工程优化，获得以下结果：

| 指标 | 初始记录 | 后续记录 | 变化 |
|---|---:|---:|---:|
| 平均工具调用/任务 | 6.07 次 | **2.50 次** | **约减少 59%** |
| 平均 LLM 调用/任务 | 5.71 次 | **3.86 次** | **约减少 32%** |
| 大型无匹配 Grep | 43.07 秒 | **0.94 秒** | **约减少 97.8%** |

前两项比较的是多个工程迭代阶段的结果，既包含工具运行时与 Workspace 修复，也包含 Agent 工具策略调整，并不是对单一策略的严格 A/B 归因。Grep 数据来自大型**合成 Fixture 的特定无匹配检索场景**，不是全部搜索请求的平均耗时。

主要优化包括重复工具探索、任务完成后过度验证、Windows junction 搜索、Shell 编码/退出码、Workspace 路径一致性以及权限与 Hook 执行路径。详细过程见 [工程优化记录](docs/ENGINEERING_NOTES.md)。

---

## 🧪 Benchmark 评测结果

### WenDream v0.3.0 冻结版本（2026-10-20）

自建 Benchmark 覆盖 **7 类30项工程任务**：

| 类别 | 用例数 |
|---|---:|
| Context | 4 |
| File | 6 |
| Tool | 6 |
| Information | 4 |
| Vision | 4 |
| Session | 3 |
| Error Recovery | 3 |
| **合计** | **30** |

每轮 2 项 SKIP（PDF Provider 支持限制，以及 Edit/Regenerate 无稳定自动化入口），因此每轮 **28项可评分**。

| 轮次 | PASS | FAIL | TIMEOUT | SKIP | 任务成功率 |
|---|---:|---:|---:|---:|---:|
| Run 1 | 27 | 1 | 0 | 2 | **96.4%** |
| Run 2 | 28 | 0 | 0 | 2 | **100%** |
| Run 3 | 28 | 0 | 0 | 2 | **100%** |

三轮累计 **84 次可评分执行，83 次通过，0 次超时**。其中 `ERROR_003` 在一轮失败，其余两轮通过，说明仍有模型行为及评测结果波动。三轮中位指标如下：

| 指标 | 三轮中位数 |
|---|---:|
| 平均工具调用次数 | **2.50 次/任务** |
| 平均 LLM 调用次数 | **3.86 次/任务** |
| 单 Case 延迟中位数 | **6.17 秒** |
| 无工具场景遵守率 | **100%**（范围 80%–100%） |

以上均是**小规模、固定的项目工程测试**，不代表公开 Agent 排行榜或生产服务 SLA。公开仓库仅保留汇总结果，未公开含个人路径和对话信息的原始 Trace。

[查看冻结测试报告](benchmark/results/final_release_wendream/FINAL_REPORT.md) · [查看公开指标](benchmark/results/final_release_wendream/README.md)

---

## 🐛 调试与验证方法

1. 使用独立 Workspace 或单 Case 复现问题；
2. 查看模型调用、工具参数、执行结果、错误和耗时；
3. 区分产品缺陷、判分逻辑问题与 Provider 行为波动；
4. 修复一项具体问题；
5. 运行对应 pytest、专项工具或权限测试；
6. 再次运行 Benchmark 并核对结果。

历史验证记录中包含 **87项安全/迁移专项测试通过**、**420项核心 Agent 测试通过**。较完整测试套件记录为 **786 passed / 2 failed / 1 skipped**，其中失败与执行环境有关，不能宣称整套测试全部通过。

```bash
# 单元测试与回归测试
python -m pytest tests -q

# 重建合成搜索 Fixture 并运行 Grep/Glob 性能测试
python benchmark/tool_bench/make_fixtures.py
python benchmark/tool_bench/run_bench.py --label manual

# 真实 Agent 测试：需要模型 API，可能产生费用
python -m benchmark.runner_validated --case-id CTX_001 --results-dir local_check
```

请勿覆盖 `benchmark/results/final_release_wendream/` 的历史冻结评测记录。

---

## 📁 项目结构

```text
WenDream/
├── wendream/
│   ├── agent.py             # Agent Loop / 流式工具执行
│   ├── client.py            # 多模型客户端
│   ├── conversation.py      # 对话消息处理
│   ├── context/             # 长输出与历史压缩
│   ├── tools/               # 文件 / Grep / Glob / Bash 等工具
│   ├── mcp/                 # MCP 适配
│   ├── memory/              # Session / Memory
│   ├── agents/              # Subagents
│   ├── teams/               # 团队协作与任务状态
│   ├── worktree/            # Git Worktree
│   ├── permissions/         # 权限检查
│   ├── hooks/               # 执行钩子
│   ├── app.py               # 终端 TUI
│   ├── remote.py            # HTTP / WebSocket 服务
│   └── web_content.py       # Web 界面
├── tests/                   # 测试用例
├── benchmark/               # 工程 Benchmark、Fixture 与汇总结果
├── scripts/                 # 辅助与审计脚本
├── config.example.yaml      # 无密钥配置模板
├── pyproject.toml           # Python 包及 CLI 入口
├── uv.lock
└── README.md
```

---

## 🛠️ 技术栈

| 部分 | 技术 |
|---|---|
| 编程语言 | Python 3.11+ |
| Agent 执行 | asyncio、流式 Tool Calling、Pydantic |
| 模型协议 | Anthropic、OpenAI、OpenAI-Compatible（如 DeepSeek） |
| 工具扩展 | MCP Stdio/HTTP、Skills、ToolSearch |
| 上下文 | 工具结果落盘、历史压缩、Session/Memory |
| 多 Agent | Subagents、Coordinator、Git Worktree |
| 终端交互 | Textual |
| Web 交互 | HTML/CSS/JavaScript、WebSocket |
| 测试 | pytest、自建 Agent Benchmark、Trace 分析 |
| 可选 OS 沙箱 | Linux bubblewrap / macOS Seatbelt |

---

## 🏷️ 关键词

`AI Agent` `LLM` `Python` `asyncio` `Tool Calling` `MCP` `Multi-Agent` `Context Engineering` `Memory` `Textual` `WebSocket` `Agent Benchmark` `Automated Testing`

---

## 📜 许可证

仅供学术用途。不可用于商业部署

---

<div align="center">

**WenDream — 让 AI Agent 不只是回答问题，也能执行任务、处理异常并接受验证。**

</div>
