# 基于 Python 的【本地编码 Agent】学习 Demo

- **状态：** 功能冻结，只接受文档与保活修复；各组件实现情况见下一节
- **记录：** [笔记](docs/NOTES.md)
- **License：** [MIT](LICENSE.md)
- **后续内核：** [agent-harness](https://github.com/cacohe/agent-harness)

## Agent 组件实现情况

### 1、核心循环

**已实现。** `AgentLoop.run_turn`：

- 用户输入写入内存会话与 transcript
- 压缩历史、组装 system、列出工具
- LiteLLM 调用 LLM
- 无 tool_use 则走 StopPolicy 结束循环
- 有则权限门 → 执行工具 → 结果当 user 消息再进循环

### 2、LLM

**部分实现。** `LiteLLMModelClient` 调 `acompletion`，按模型 id 前缀读 DeepSeek / OpenAI / Anthropic / Gemini 的环境变量密钥；只配一家时可省略 `--model`。

- LiteLLM 作为 LLM 网关，统一 LLM 调用
- 每次请求显式带上当前 messages / tools / system
- 无流式结算、无调用重试、无计费

### 3、工具调用

**部分实现。** 

- 编码 CLI 默认注册 `read_file` / `write_file` / `edit_file` / `glob` / `exec` 与 `load_skill`；
- schema 经 ToolRegistry 交给模型。
- MCP client 在库里（`attach_mcp`），**不**挂到默认 REPL。鉴权见第 7 节；
- 工具执行默认 60s 超时，没有工具自动重试
- 未知结果不会再发一遍，也没有崩溃续跑

### 4、状态管理

**部分实现。** 

- 本轮消息在内存 `SessionState`；
- 每条消息追加 JSONL transcript（可从jsonl文件回放，CLI **不会**在下次启动时 load 回会话）。
- 不是 turn/step 有限状态机（没有明确的状态定义），没有 resume，进程退出即丢对话。

### 5、上下文管理

**部分实现。** 
- `ContextAssembler` 拼 system（人设 + pack 片段 + 技能名/摘要 + 记忆召回 + workspace 路径）；
- 工具 schema 单独放进模型请求。
- `BudgetCompactor` 按字符估算 token：截断过长 tool_result、丢掉较早工具正文、超预算再折叠头部历史。
- 无按项 token 配额、无向量 rerank、无 Prompt Cache、阈值不跟模型窗口动态对齐。

### 6、记忆管理

**部分实现。** 

- 短期记忆即本进程 `SessionState` + 当前 system/工具表
- 长期记忆默认开：工作区 `.harness/coding-agent/` 下 Markdown 文件 + `MEMORY.md` 索引，按关键词启发式 `select_for_prompt` 注入 system（`## Relevant memories`）。
- SQLite 任务库可选。
- 无 PG/Qdrant/OSS，无自动过期。

### 7、安全约束

**部分实现。** 

- `WorkspaceGuard` 把文件工具限制在工作区；
- `PermissionGate` 按风险：读允许、写/执行询问、网络拒绝。
- 询问走 `StdinApproval`（终端 `y/N`），`--yes` 为 `AutoApprove`。
- 权限表在进程内，不是冻结快照；无独立权限权威、无逐步短时许可、无积分记账。
- `StdinApproval` 的 `input()` 会堵住事件循环。

### 8、Sandbox 执行

**部分实现（仅本地 exec）。** 
- 可选 `--sandbox auto|direct|bubblewrap|docker`；
- 没有 bwrap/docker 时 `auto` 等于本机直接执行

### 9、不同类型的任务执行

| 类型 | 本仓库 |
| --- | --- |
| 普通对话 | **已实现**，REPL 一轮 `session.run` |
| 隔离沙箱任务 | **仅** `exec` 可选 bwrap/docker，无独立任务通道 |
| 长期任务 | 库内有 teams，编码 CLI **默认关闭** |
| 周期任务 | 库内有 cron，编码 CLI **默认关闭** |

无 HTTP 服务、无登录、无多用户。

### 10、可观测性

**部分实现。** 

- 工具调用可打 verbose 日志；
- transcript JSONL 记消息；
- Python logging。
- 无逐步状态机事件、无带权限的内容级审计与留存。

### 11、反馈验证

- **未实现**独立校验器。工具失败以 `ToolResult.is_error` 回给模型，靠模型下一轮自行纠正
- 无对行动结果的程序化验证/重试。

### 12、系统提示词管理

**部分实现。** 
- 固定拼接：`CODING_SYSTEM_PROMPT` + CodingPack 片段 + 技能目录 + 记忆 + workspace。
- 无分级/按序组装策略

### 13、终止控制

**部分实现。** 
- 模型不再调工具即结束；
- `MaxIterationStopPolicy` 只在这一刻检查 `max_iterations`，连续 tool_use 可以超过该上限。
- 无取消令牌；
- Ctrl+C 主要在两次 `coding >>` 之间生效。

### 14、ACP 能力

**未实现。** 不与外部 Agent 通信。


## 技术栈

| 技术 | 在本项目中的应用 |
| --- | --- |
| Python 3.12 | 基础语言 |
| [uv](https://docs.astral.sh/uv/) | 依赖与运行 |
| [LiteLLM](https://www.litellm.ai/) | 聊天补全；按模型 id 前缀读对应 API Key |
| Pydantic | 配置、权限规则、消息块 |
| SQLite | 任务库（可选 mailbox） |
| pytest / Ruff | 单元测试与静态检查 |
| [GitHub Actions](https://github.com/features/actions) | CI：Ruff + pytest |

## 项目结构

```
coding-agent/
├── .github/workflows/ci.yml
├── docs/
│   └── NOTES.md
├── harness/                 # 循环、权限、工具、记忆
│   ├── core/                # AgentLoop、会话、审批
│   ├── models/              # LiteLLM 适配
│   └── tools/ memory/ skills/ mcp/ …
├── agents/coding/           # 编码 Agent 的 Pack 与 CLI
├── coding_agent.py
├── skills/                  # 示例 SKILL.md
├── tests/
├── .env.example
├── LICENSE.md
└── pyproject.toml
```

## 快速开始

需要 **Python 3.12+** 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/cacohe/coding-agent.git
cd coding-agent
uv sync --group dev
cp .env.example .env
```

Windows PowerShell 可用 `Copy-Item .env.example .env`。在 `.env` 里填入**一家**厂商的 API Key。

```bash
uv run python coding_agent.py
```

出现 `coding >>` 后输入任务。写文件和执行命令会询问 `y/N`。可信工作区可加 `--yes`。

## CLI

```bash
uv run python coding_agent.py --workspace /path/to/project
uv run python coding_agent.py --model deepseek/deepseek-chat --yes
```

| 参数 | 说明 |
| --- | --- |
| `--workspace` | 项目目录，默认当前目录 |
| `--model` | LiteLLM 模型 id；省略则按唯一的 API Key 推断 |
| `--yes` | 自动批准写文件 / 执行命令 |
| `--sandbox` | `auto`（默认）/ `direct` / `bubblewrap` / `docker` |
| `--quiet` | 隐藏每次工具调用日志 |
| `--api-base` | 自定义网关才需要；官方厂商不必填 |

密钥只放在 `.env`，不要写进命令行。多家密钥同时存在时必须显式 `--model`。

## 常用命令

```bash
uv sync --group dev
uv run python coding_agent.py
uv run pytest -q
uv run ruff check harness agents tests coding_agent.py
uv run ruff format --check harness agents tests coding_agent.py
```

测试使用 `tests/fakes.py` 的假模型，不调用真实 API。

## CI（GitHub Actions）

配置文件：`.github/workflows/ci.yml`

**触发条件**：向 `main` / `master` push 或 Pull Request。

| 步骤 | 说明 |
| --- | --- |
| `uv sync --frozen --group dev` | 锁定版本安装 |
| Ruff check / format | 静态检查 |
| `pytest -q` | 假模型单元测试 |

CI **不会**调用真实模型。

## 范围与限制

- 单进程本地 REPL，无登录、无多租户。
- 同一进程内可以多轮对话；重启 CLI 后 transcript 不恢复进 `SessionState`。
- 没有取消令牌；Ctrl+C 主要在两次提问之间生效。
- `max_iterations` 只在模型不再调工具时检查；连续调工具可以超过该上限。
- Windows 上 `auto` 沙箱通常是 `direct`。
- 生产级循环、resume、取消结算见 [agent-harness](https://github.com/cacohe/agent-harness)。本仓库冻结后不要再扩 teams / cron / 评测。

## License

[MIT](LICENSE.md)
