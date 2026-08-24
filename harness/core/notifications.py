"""通知注入：把后台任务 / cron 触发的事件写回 messages。

对齐 learn-claude-code s11/s12：在下一轮 LLM 调用前注入，
不改动 AgentLoop 的主控制流。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from harness.core.types import user_text

if TYPE_CHECKING:
    from harness.core.session import SessionState
    from harness.core.transcript import TranscriptStore

logger = logging.getLogger(__name__)


class NotificationSource(Protocol):
    """可被 NotificationHub 抽取的通知源。"""

    def drain(self) -> list[str]:
        """取出并清空待注入的文本通知。"""
        ...


class NotificationHub:
    """聚合多个通知源，在模型调用前注入为 user 消息。"""

    def __init__(
        self,
        sources: list[NotificationSource] | None = None,
        *,
        transcript: TranscriptStore | None = None,
    ) -> None:
        self.sources: list[NotificationSource] = list(sources or [])
        self.transcript = transcript

    def add_source(self, source: NotificationSource) -> None:
        """追加通知源（后台 runner、cron 等）。"""
        self.sources.append(source)

    async def inject(self, session: SessionState) -> int:
        """把所有待通知写入 session.messages；返回注入条数。"""
        notes: list[str] = []
        for source in self.sources:
            try:
                notes.extend(source.drain())
            except Exception:
                logger.exception("notification source drain failed")
        if not notes:
            return 0
        # 合并为一条，减少轮次噪音
        body = "\n".join(notes)
        msg = user_text(f"<harness_notifications>\n{body}\n</harness_notifications>")
        session.messages.append(msg)
        if self.transcript is not None:
            await self.transcript.append(session.session_id, msg)
        logger.info("injected %d notifications into session %s", len(notes), session.session_id)
        return len(notes)
