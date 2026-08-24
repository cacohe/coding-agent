"""Harness 配置。

运行参数一律由构造函数传入。凭证不在此绑定任何供应商前缀：
- 未传 api_key / api_base 时，由 LiteLLM 按 model 自行读取对应厂商环境变量
  （如 OPENAI_API_KEY、ANTHROPIC_API_KEY、GEMINI_API_KEY、AZURE_API_KEY 等）
- 仅在走统一网关/代理时，Host 可显式传入 api_key / api_base 覆盖
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class HarnessConfig(BaseModel):
    """运行时配置（构造参数为主；无自创环境变量命名）。"""

    # 模型 id：LiteLLM 路由串，如 "openai/gpt-4o"、"anthropic/claude-sonnet-4-5"
    model_id: str = "openai/gpt-4o"
    max_tokens: int = 8000
    # 可选覆盖；为 None 时交给 LiteLLM 按厂商环境变量解析
    api_base: str | None = None
    api_key: str | None = None

    # 工作区与存储
    workspace: Path = Field(default_factory=Path.cwd)
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / ".harness")
    transcript_dir: Path | None = None

    # 循环安全阀
    max_iterations: int = 40
    compact_max_tokens: int = 50_000

    # 系统人设
    system_prompt: str = (
        "You are a careful agent. Prefer tools over speculation. "
        "Stay inside the workspace. Act, then briefly report."
    )

    # 是否对写/执行自动批准（开发方便；生产应 false）
    auto_approve: bool = False

    # 能力开关
    enable_memory: bool = True
    enable_tasks: bool = True
    enable_cron: bool = True
    # 启用 GoalStopPolicy（需在 session.extras['goal'] 设置目标）
    enable_goal_stop: bool = False
    goal_max_continues: int = 5

    # 团队协作
    enable_teams: bool = False
    # auto | direct | bubblewrap | docker
    sandbox: str = "auto"

    def resolved_transcript_dir(self) -> Path:
        """transcript 目录默认落在 data_dir 下。"""
        if self.transcript_dir is not None:
            return self.transcript_dir
        return self.data_dir / "transcripts"

    def resolved_memory_dir(self) -> Path:
        """记忆目录。"""
        return self.data_dir / "memory"

    def resolved_tasks_db(self) -> Path:
        """任务 SQLite 路径。"""
        return self.data_dir / "tasks.db"

    def resolved_mailbox_db(self) -> Path:
        """队友邮箱 SQLite 路径。"""
        return self.data_dir / "mailbox.db"
