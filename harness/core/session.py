"""会话状态与 Session 门面。

Session 是 host 的主要交互对象：持有 messages、workspace、迭代计数，
并委托 AgentLoop 执行单轮用户输入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from harness.core.types import Message, TurnResult

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """可变会话状态（循环与各 Port 共享）。"""

    session_id: str
    workspace: Path
    messages: list[Message] = field(default_factory=list)
    # 当前 turn 内的模型迭代次数（每次 LLM 调用 +1）
    iteration_count: int = 0
    # 当前 turn 工具调用次数
    tool_call_count: int = 0
    # 最近一条用户原文，供 memory / context 使用
    latest_user_text: str = ""
    # 扩展槽：packs / MCP 可挂自定义状态
    extras: dict[str, Any] = field(default_factory=dict)

    def reset_turn_counters(self) -> None:
        """新用户输入开始时清零本轮计数。"""
        self.iteration_count = 0
        self.tool_call_count = 0


class Session:
    """面向 host 的会话句柄。"""

    def __init__(self, state: SessionState, *, loop: Any, harness: Any) -> None:
        # loop / harness 用 Any 避免循环导入；运行时由 Harness 注入
        self.state = state
        self._loop = loop
        self._harness = harness

    @property
    def session_id(self) -> str:
        return self.state.session_id

    @property
    def messages(self) -> list[Message]:
        return self.state.messages

    async def run(self, user_input: str) -> TurnResult:
        """处理一条用户输入直到 StopPolicy 允许返回。"""
        logger.info("session %s run: %s", self.session_id, user_input[:80])
        return await self._loop.run_turn(self.state, user_input)


def new_session_id() -> str:
    """生成短会话 id。"""
    return uuid4().hex[:12]
