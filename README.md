# 基于 Python 的【本地编码 Agent】学习 Demo

- **状态：** 功能冻结，只接受文档与保活修复；已实现功能见下一节
- **记录：** [笔记](docs/NOTES.md)
- **License：** [MIT](LICENSE.md)
- **后续内核：** [agent-harness](https://github.com/cacohe/agent-harness)

Python 包名仍是 `agent-harness`。模型负责决策；本仓库的循环负责权限与工具执行。不要把这里继续做成第二套生产运行时。

## 已实现功能

- 本地 REPL：`coding_agent.py`，提示符 `coding >>`，`/quit` 退出
- 在工作区 jail 内读文件、写文件、替换、glob、执行命令
- 写文件和执行命令默认 stdin `y/N` 审批；`--yes` 自动批准
- 命令沙箱：`auto` / `direct` / `bubblewrap` / `docker`；没有 bwrap/docker 时 `auto` 等于本机直接执行
- 扫描 `skills/*/SKILL.md`，模型用 `load_skill` 按需加载
- 文件记忆与 SQLite 任务库写在工作区 `.harness/coding-agent/`
- 对话 transcript 追加写入 JSONL（审计/回放，**不会**在下次启动时恢复成对话）
- LiteLLM 多厂商：DeepSeek / OpenAI / Anthropic / Gemini；只配一家密钥时可省略 `--model`
- 假模型单元测试 + GitHub Actions（Ruff + pytest）

未实现：会话 resume、中途取消、模型重试、HTTP 服务、登录、多用户。编码 CLI 默认关闭 cron 与 teams；MCP 需手动 `attach_mcp`，不在默认 REPL 里。

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
