# agent-harness

Harness 核心 + 可插拔能力 + 示例 agents。模型负责决策；核心负责循环、权限与工具执行。

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

## 快速开始

```bash
uv sync --group dev
cp .env.example .env   # 填入一家厂商的 API Key
uv run python coding_agent.py
```

出现 `coding >>` 后输入任务，`/quit` 退出。写文件和执行命令默认会询问 `y/N`；可信工作区可加 `--yes`。

## 配置

密钥只放在 `.env`（已加入 `.gitignore`），不要写进命令行。未传 `--model` 时，若环境里只有一家密钥，CLI 会选用对应默认模型：

| 环境变量 | 默认模型 |
|----------|----------|
| `DEEPSEEK_API_KEY` | `deepseek/deepseek-chat` |
| `OPENAI_API_KEY` | `openai/gpt-4o` |
| `ANTHROPIC_API_KEY` | `anthropic/claude-sonnet-4-5` |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `gemini/gemini-2.0-flash` |

多家密钥同时存在时必须显式 `--model`。官方 API 不需要 `--api-base`；仅自定义网关才传。

会话数据写在工作区 `.harness/coding-agent/`（transcript、memory、tasks）。

## CLI

```bash
uv run python coding_agent.py --workspace /path/to/project
uv run python coding_agent.py --model deepseek/deepseek-chat --yes
```

| 参数 | 说明 |
|------|------|
| `--workspace` | 项目目录，默认当前目录 |
| `--model` | LiteLLM 模型 id；省略则按 API Key 推断 |
| `--yes` | 自动批准写文件 / 执行命令 |
| `--sandbox` | `auto`（默认）/ `direct` / `bubblewrap` / `docker` |
| `--quiet` | 隐藏每次工具调用日志 |
| `--api-base` | 自定义 API 地址，官方厂商不必填 |

无 `bwrap` / `docker` 时，`auto` 等于本机直接执行。

## 测试

```bash
uv run pytest -q
uv run ruff check harness agents tests coding_agent.py
uv run ruff format --check harness agents tests coding_agent.py
```

推送与 PR 会跑 GitHub Actions（Ruff + pytest）。

## 目录

```text
agent-harness/
  harness/               # 核心 + 可插拔能力
    core/                # 循环与端口
    models/              # 厂商适配（非 core）
    tools/ memory/ …     # 可插拔实现
  agents/coding/         # 示例：编码 Agent 的 Pack 与装配
  coding_agent.py        # 该示例的 CLI
  skills/ tests/
```
