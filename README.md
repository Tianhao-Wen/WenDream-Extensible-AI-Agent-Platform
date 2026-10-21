<div align="center">
# 🤖 WenDream — Extensible AI Agent Platform

**A tool-using AI Agent for natural-language tasks, file operations, code exploration, and multi-step workflows**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Agent](https://img.shields.io/badge/Architecture-LLM%20Agent-purple?style=flat-square)](#-system-architecture)
[![MCP](https://img.shields.io/badge/Tools-MCP%20%2B%20Tool%20Calling-orange?style=flat-square)](#-mcp--extensible-tools)
[![UI](https://img.shields.io/badge/Interface-TUI%20%2B%20Web-green?style=flat-square)](#-web--terminal-interfaces)
[![Benchmark](https://img.shields.io/badge/Benchmark-30%20Cases-informational?style=flat-square)](#-benchmark-results)
[![Timeouts](https://img.shields.io/badge/Frozen%20Runs-0%20Timeouts-brightgreen?style=flat-square)](#-benchmark-results)

*Streaming Tool Calling · Context Management · MCP · Multi-Agent · Web UI · Engineering Benchmark*

**English** · [简体中文](README.zh-CN.md)

</div>

---

## 🎬 Web Demo

> **Web walkthrough coming soon.** A screen recording of the actual WenDream browser interface will be added here after it has been recorded and reviewed.

The planned demonstration will show an end-to-end, **real model and tool execution** workflow rather than a scripted UI mock-up:

1. **Start WenDream Web UI** and create a new conversation.
2. **Ask a natural-language question** and inspect the streamed response.
3. **Analyze a local text or code file** and display any tool activity in the interface.
4. **Request a safe file operation**, review the resulting output, and continue with a follow-up question.
5. **Switch back to the session** to show conversation history and continuity.

<!--
WHEN THE VIDEO IS READY:
1. Save a sanitized video at assets/wendream_web_demo.mp4 (or use a permanent video URL).
2. Save a real screenshot/cover image at assets/demo_thumbnail.png.
3. Replace the "coming soon" paragraph above with:

[![WenDream Web Demo](./assets/demo_thumbnail.png)](./assets/wendream_web_demo.mp4?raw=1)

[▶ Watch the full WenDream Web demo](./assets/wendream_web_demo.mp4?raw=1)

Never record API keys, local credentials, private conversations, or sensitive file paths.
-->

---

## ✨ Key Features

| Feature | Description |
|---|---|
| 🧠 **LLM-driven Agent** | Uses model responses and tool calls to handle user requests across multiple execution steps. |
| ⚡ **Streaming execution** | Streams assistant output and can submit eligible tool calls during model generation. |
| 🛠️ **File & Shell tools** | Provides file reading, writing, editing, Grep/Glob search, and Bash execution. |
| 🔎 **ToolSearch / lazy exposure** | Keeps common file tools accessible while exposing exploratory tools such as Bash/Glob on demand. |
| 🔌 **MCP integration** | Connects external tools using MCP with Stdio and HTTP transport support. |
| 🧩 **Context compression** | Spills oversized tool results to disk and compacts older conversation history when needed. |
| 💾 **Session & memory** | Supports persistent conversations, session resumption, and project-related memory. |
| 👥 **Multi-Agent workflows** | Includes subagents, coordination facilities, and optional Git worktree isolation. |
| 🖥️ **TUI & Web UI** | Offers a Textual terminal interface and a browser interface backed by WebSocket. |
| 🛡️ **Permission checks & hooks** | Enforces permission decisions, hook interception, and remote access checks. |
| 🧪 **Agent Benchmark** | Includes 30 local end-to-end engineering cases with execution evidence and repeat-run analysis. |

---

## 🏗️ System Architecture

```text
                   ┌──────────────────────────────┐
                   │             User             │
                   │    Terminal TUI / Web UI      │
                   └──────────────┬───────────────┘
                                  │ request / events
                                  ▼
                   ┌──────────────────────────────┐
                   │       WenDream Agent         │
                   │ Context + Execution Policies │
                   └──────────┬──────┬────────────┘
                              │      │
                 model calls  │      │ tool decisions
                              ▼      ▼
                 ┌────────────────┐ ┌────────────────────────┐
                 │ LLM Providers  │ │ Tool Registry / Search │
                 │ Anthropic      │ │ Read / Write / Edit    │
                 │ OpenAI         │ │ Grep / Glob / Bash     │
                 │ OpenAI-compat  │ │ MCP / Skills / Agents  │
                 └────────────────┘ └────────────┬───────────┘
                                                  │
                                        ┌─────────▼──────────┐
                                        │ Permission + Hooks │
                                        │ Workspace handling │
                                        └─────────┬──────────┘
                                                  │
                                        ┌─────────▼──────────┐
                                        │ Actual tool result │
                                        └─────────┬──────────┘
                                                  │
                                ┌─────────────────┘
                                ▼
                    Agent consumes results → response

      Session / Memory / Context     Benchmark / Traces / Regression
        (conversation state)          (engineering validation)
```

### Typical task flow

```text
User message
    ↓
Build conversation context and available tool schemas
    ↓
LLM reasoning / streamed tool-call generation
    ↓
Permission and hook checks
    ↓
Execute allowed tool(s) → capture tool result / error
    ↓
Update context → continue or complete the task
    ↓
Stream final answer to TUI / Web UI
```

The Agent can use multiple execution steps for one request. Tool use is conditional: simple conversational requests do not necessarily require a filesystem or Shell action.

---

## ⚡ Agent Execution & Tool Calling

WenDream's execution path supports model streaming and asynchronous tool execution. Its `StreamingExecutor` collects eligible tool calls and results as the model response progresses; execution remains subject to the tool implementation and permission checks.

**Key engineering decisions in this project:**

- **Conversation-first:** answer straightforward questions without unnecessarily inspecting the workspace.
- **Completion Contract:** make task completion explicit to reduce repeated checks after the requested work is done.
- **ToolSearch / lazy exposure:** keep exploration-oriented tools out of the initial schema until they are needed.
- **Trace-based diagnosis:** use actual calls, results, errors, and timings to understand inefficient Agent behavior.

> Concurrency does **not** mean every action runs in parallel. Side effects, tool safety, and permission requirements still matter.

---

## 🧩 Context Management & Sessions

The codebase supports two complementary techniques for handling large conversations.

### Layer 1 — Oversized tool-result spillover

When a single tool result exceeds **50,000 characters**, the full content can be stored on disk while the prompt receives a shorter preview (configured at **2,000 characters**) and a reference to the saved result. This avoids repeatedly inserting very large outputs into the active conversation.

### Layer 2 — History compaction

When the active context approaches its capacity, the system can summarize older conversation history while retaining recent messages. Current configuration includes:

| Parameter | Value | Purpose |
|---|---:|---|
| Single-result threshold | `50,000` characters | Trigger large-result handling |
| Tool-result preview | `2,000` characters | Keep a readable excerpt in context |
| Recent-history target | `10,000` tokens | Preserve recent context during compaction |
| Soft safety margin | `13,000` tokens | Begin guarded auto-compaction |
| Hard safety margin | `3,000` tokens | Trigger stricter compaction behavior |

Session storage supports returning to previous conversations. Compaction is a lossy summary of old context, **not a guarantee that every historical detail remains verbatim**.

---

## 🔌 MCP & Extensible Tools

The Tool Registry provides built-in operations and a path for external integrations.

| Tool / capability | Example use |
|---|---|
| `ReadFile` | Inspect a source file or text document |
| `WriteFile` / `EditFile` | Create or modify workspace files |
| `Grep` / `Glob` | Search file content or names |
| `Bash` | Execute an approved local command |
| `ToolSearch` | Discover a deferred tool when needed |
| MCP tools | Use configured external MCP servers |
| Skills / Subagents | Extend task workflows and delegate subtasks |

**Lazy exposure in the current default registry:** `ReadFile`, `WriteFile`, `EditFile`, and `Grep` stay directly available; exploration tools including `Bash` and `Glob` can be exposed through `ToolSearch` when needed. This balances token usage against the overhead of searching for tools.

MCP servers require separate configuration. The repository does **not** ship with live third-party accounts connected.

---

## 👥 Multi-Agent & Worktree Support

The framework includes subagent execution, coordination and team communication modules, plus optional Git worktree workflows.

```text
                       Main Agent
                            │
               ┌────────────┼────────────┐
               ▼            ▼            ▼
          Explore Agent  Plan Agent   General Agent
               │            │            │
               └────────────┼────────────┘
                            ▼
                   Results / Task State
                            │
                            ▼
                      Main Agent
```

Agent instances may use separate working contexts, with worktrees available for tasks that benefit from workspace separation. These are **optional capabilities**, not prerequisites for ordinary chat or single-Agent tasks.

---

## 🖥️ Web & Terminal Interfaces

### Browser interface

The Web UI supports conversational input, streamed assistant output, tool execution feedback, session navigation, and supported file or image attachments. Input capabilities depend on the selected LLM provider; for example, PDF support is not universal.

Run the local server:

```bash
wendream --remote
```

Then visit **http://127.0.0.1:18888** in your browser.

### Terminal interface

```bash
wendream
```

### Non-interactive execution

```bash
wendream -p "Summarize the structure of this project."
```

In non-interactive mode, actions that require an `ASK` permission decision are denied by default. Avoid bypassing permissions in untrusted workspaces.

---

## 🔐 Permissions & Execution Safety

WenDream includes permission modes, rule checks, hook interception, Workspace-aware file tools, and remote-access protections.

- **Tool permission checks:** prompt, allow, or deny actions according to the configured mode and rules.
- **Hooks:** include `pre_tool_use` checks in streaming execution paths.
- **Remote access:** bind to loopback by default; non-loopback binding requires a configured token unless an explicit unsafe override is used.
- **Origin checks and HTML sanitization:** protect the browser-facing execution path against common unsafe access/rendering patterns.
- **Platform-dependent sandboxing:** bubblewrap is available on supported Linux systems; Seatbelt on supported macOS systems. **Windows has no equivalent OS-level sandbox in this project.**

> Tool permission checks and Workspace restrictions are **not** a substitute for a fully hardened container or OS-level isolation. Review shell commands and do not run untrusted inputs with bypass permissions.

---

## 🚀 Getting Started

### Requirements

- **Python 3.11+** and `pip` (or a compatible Python environment manager)
- An accessible **LLM provider API** and valid API credentials
- A modern web browser for Web UI mode
- Git when using Git worktree features
- Optional platform tools for MCP, team backends, or OS sandbox features

### 1. Clone the repository

```bash
git clone <YOUR-WENDREAM-REPOSITORY-URL>
cd WenDream
```

Replace the placeholder URL with your public GitHub repository address.

### 2. Install dependencies

```bash
python -m pip install -e .
```

For test development, install the optional test dependencies as well:

```bash
python -m pip install -e . pytest pytest-asyncio
```

### 3. Configure the model

Create a private local config using the included template:

**macOS / Linux**

```bash
mkdir -p .wendream
cp config.example.yaml .wendream/config.yaml
```

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Force .wendream | Out-Null
Copy-Item config.example.yaml .wendream/config.yaml
```

The template uses an **OpenAI-compatible DeepSeek provider** and reads its API key from `${DEEPSEEK_API_KEY}`. Set the value in the current terminal environment. Do not commit keys to Git.

**macOS / Linux (hidden terminal input)**

```bash
read -rsp 'DEEPSEEK_API_KEY: ' DEEPSEEK_API_KEY; echo
export DEEPSEEK_API_KEY
```

**Windows PowerShell (hidden terminal input)**

```powershell
$secureKey = Read-Host "DEEPSEEK_API_KEY" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
```

The local `.wendream/` directory is ignored by Git. You can edit `config.yaml` to select another supported provider protocol or model.

### 4. Start WenDream

```bash
wendream --version
wendream                 # Terminal TUI
wendream --remote        # Browser UI at http://127.0.0.1:18888
```

> Browser and model calls run locally through the configured application server, but LLM requests go to the provider you choose and may incur API charges.

---

## 🎥 Suggested Web Demo Workflow

When recording the public demo, use a small **non-sensitive sample workspace**, not personal files or private credentials. This sequence demonstrates visible product behavior:

| Step | Example user prompt | What to show |
|---|---|---|
| 1. Chat | “Explain what an AI Agent can do in three points.” | Normal response without forced tool exploration |
| 2. File reading | “Read `demo_notes.md` and summarize its key requirements.” | Tool call, file content, streamed summary |
| 3. Follow-up | “Turn those requirements into a short action list.” | Multi-turn context continuity |
| 4. Safe file operation | “Write the action list to `demo_tasks.md`.” | Tool execution and resulting file |
| 5. Session | Open a previous session and continue the discussion. | Session persistence and Web UI navigation |

**Video checklist:** hide API keys and local personal paths; use dummy materials; show real tool results; keep the recording concise; avoid presenting unexecuted features as demonstrated.

---

## 📊 Performance & Engineering Improvements

Development focused on reducing unnecessary model/tool calls and fixing execution-path problems found through traces and targeted tool benchmarks.

| Engineering metric | Initial measurement | Later measurement | Observed change |
|---|---:|---:|---:|
| Mean tool calls / task | `6.07` | **`2.50`** | **≈ 59% fewer** |
| Mean LLM calls / task | `5.71` | **`3.86`** | **≈ 32% fewer** |
| Large-fixture Grep, no match | `43.07 s` | **`0.94 s`** | **≈ 97.8% less time** |

**How to read these numbers:** the first two compare an early baseline with a later frozen release across **several engineering stages**, including tool runtime fixes, workspace handling, and Agent strategies. They do not isolate a single optimization as the only cause. The Grep figure comes from one **specific synthetic no-match search scenario**, not all file-search operations.

The main changes investigated included:

- Reducing repeated tool exploration and post-completion verification.
- Making exploratory tools available only when needed.
- Fixing the effective Workspace root for file, search, and Shell tools.
- Handling Windows junction traversal and Shell encoding / exit-code issues.
- Improving non-interactive permissions, hook execution, and browser access controls.

See [Engineering Notes](docs/ENGINEERING_NOTES.md) and the [Benchmark Guide](benchmark/README.md) for evaluation details.

---

## 🧪 Benchmark Results

### Frozen WenDream v0.3.0 — 20 October 2026

The local engineering benchmark contains **30 cases across 7 categories**:

| Category | Cases |
|---|---:|
| Context | 4 |
| File operations | 6 |
| Tool calling | 6 |
| Information processing | 4 |
| Vision | 4 |
| Session | 3 |
| Error recovery | 3 |
| **Total** | **30** |

Two cases were skipped in each run: provider-dependent PDF handling and a session edit/regenerate workflow without a stable automated interface. **28 cases were therefore scored per run.**

| Frozen run | Passed | Failed | Timed out | Skipped | Task success |
|---|---:|---:|---:|---:|---:|
| Run 1 | 27 | 1 | 0 | 2 | **96.4%** |
| Run 2 | 28 | 0 | 0 | 2 | **100.0%** |
| Run 3 | 28 | 0 | 0 | 2 | **100.0%** |

**Combined:** **83 / 84 scored executions passed**, with **0 timeouts** across the three runs. One case (`ERROR_003`) varied in its pass/fail outcome across runs.

| Additional metric | Three-run median |
|---|---:|
| Mean tool calls / task | **2.50** |
| Mean LLM calls / task | **3.86** |
| Median case latency | **6.17 s** |
| Tool-free compliance | **100%** (range **80–100%**) |

These figures describe a **small, fixed, project-specific engineering test set**, not a public Agent leaderboard or a production-service latency guarantee. The public repository includes aggregate evidence, while raw session traces and private local paths have been intentionally excluded.

[📄 Frozen Benchmark Report](benchmark/results/final_release_wendream/FINAL_REPORT.md) · [📁 Published Metrics](benchmark/results/final_release_wendream/README.md)

---

## 🐛 Debugging & Validation Strategy

Development used targeted tests before running the full Agent benchmark:

1. **Reproduce** a failure using an isolated test workspace or a saved failing case.
2. **Inspect** tool arguments, results, errors, permissions, and phase timings.
3. **Distinguish** product bugs from evaluator mistakes or provider fluctuations.
4. **Change** one scoped behavior or tool implementation.
5. **Run** focused unit/permission/tool tests.
6. **Repeat** the benchmark and compare the measured result.

The uploaded validation archive reports **87 passing safety/migration tests** and **420 passing core Agent tests**. A broader historical suite recorded **786 passed, 2 environment-related failures, and 1 skipped**; this should **not** be described as a clean full-suite pass or as a fresh cross-platform rerun.

### Reproducing local tests

```bash
python -m pytest tests -q

# Generate synthetic Grep/Glob fixtures before the performance test:
python benchmark/tool_bench/make_fixtures.py
python benchmark/tool_bench/run_bench.py --label manual

# Real-Agent benchmark: requires a configured model and API usage.
python -m benchmark.runner_validated --case-id CTX_001 --results-dir local_check
```

Do not overwrite the frozen `final_release_wendream` results directory when running new experiments.

---

## 📁 Project Structure

```text
WenDream/
├── wendream/                     # AI Agent application
│   ├── agent.py                  # Agent loop and streaming tool execution
│   ├── client.py                 # Model-provider clients
│   ├── conversation.py           # Conversation messages
│   ├── context/                  # Result spillover / context compaction
│   ├── tools/                    # File, search, Shell and tool search
│   ├── mcp/                      # MCP client and external integrations
│   ├── memory/                   # Session and memory management
│   ├── agents/                   # Subagent support
│   ├── teams/                    # Coordination and team communication
│   ├── worktree/                 # Git worktree support
│   ├── permissions/              # Permission rules and checks
│   ├── hooks/                    # Tool execution hooks
│   ├── app.py                    # Terminal TUI
│   ├── remote.py                 # HTTP / WebSocket application server
│   └── web_content.py            # Embedded browser interface
├── tests/                        # Unit and regression tests
├── benchmark/
│   ├── cases/                    # Validated task definitions
│   ├── fixtures/                 # Small local test data
│   ├── tool_bench/               # Synthetic search-tool performance tests
│   ├── results/                  # Published frozen-run summaries
│   └── runner_validated.py       # Real-Agent benchmark entrypoint
├── scripts/                      # Maintenance / audit utilities
├── docs/                         # Attribution and engineering notes
├── config.example.yaml           # No-secret configuration template
├── pyproject.toml                # Package metadata and CLI entrypoint
├── uv.lock                       # Dependency lock
└── README.md
```

---

## 🛠️ Tech Stack

| Component | Technology |
|---|---|
| Language | **Python 3.11+** |
| Agent orchestration | Async Agent loop, `asyncio`, streaming tool execution |
| Provider APIs | Anthropic, OpenAI, OpenAI-compatible (including DeepSeek configuration) |
| Structured validation | Pydantic, JSON, YAML |
| Tools / integration | Tool Calling, MCP Stdio/HTTP, Skills |
| Context / persistence | Tool-result spillover, history compaction, Sessions / Memory |
| Multi-Agent | Subagents, team coordination, optional Git worktrees |
| Terminal UI | Textual |
| Browser UI | Embedded HTML/CSS/JavaScript + WebSocket |
| Testing | pytest, custom Agent Benchmark, Trace and performance fixtures |
| Optional OS sandbox | bubblewrap (supported Linux) / Seatbelt (supported macOS) |

---

## 🏷️ Keywords

`AI Agent` `LLM` `Python` `asyncio` `ReAct-style` `Tool Calling` `MCP` `Multi-Agent` `Context Engineering` `Memory` `Textual` `WebSocket` `Agent Benchmark` `Trace Analysis` `Automated Testing`

---


## 📜 License

For academic use only. Not for commercial deployment.

---

<div align="center">

**WenDream — building AI Agents that can execute, recover, and be evaluated.**

</div>
