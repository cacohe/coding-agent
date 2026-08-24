"""Transcript 回放与会话度量。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harness.core.transcript import JsonlTranscriptStore, TranscriptStore
from harness.core.types import Message, ToolResultBlock, ToolUseBlock


@dataclass
class SessionMetrics:
    """从 transcript 提取的聚合指标。"""

    session_id: str
    message_count: int = 0
    user_turns: int = 0
    assistant_turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    tools_used: list[str] = field(default_factory=list)
    # 工具名 -> 调用次数
    tool_counts: dict[str, int] = field(default_factory=dict)

    @property
    def tool_error_rate(self) -> float:
        """工具错误率。"""
        if self.tool_calls == 0:
            return 0.0
        return self.tool_errors / self.tool_calls


class TranscriptReplayer:
    """加载并分析 JSONL transcript。"""

    def __init__(self, store: TranscriptStore) -> None:
        self.store = store

    @classmethod
    def from_dir(cls, transcript_dir: Path) -> TranscriptReplayer:
        """从目录构造。"""
        return cls(JsonlTranscriptStore(transcript_dir))

    async def load(self, session_id: str) -> list[Message]:
        """加载完整消息列表。"""
        return await self.store.load(session_id)

    async def summarize(self, session_id: str) -> SessionMetrics:
        """统计一轮会话的工具与轮次指标。"""
        messages = await self.load(session_id)
        metrics = SessionMetrics(session_id=session_id, message_count=len(messages))
        for msg in messages:
            if msg.role == "user":
                metrics.user_turns += 1
            elif msg.role == "assistant":
                metrics.assistant_turns += 1
            if isinstance(msg.content, str):
                continue
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    metrics.tool_calls += 1
                    metrics.tools_used.append(block.name)
                    metrics.tool_counts[block.name] = metrics.tool_counts.get(block.name, 0) + 1
                elif isinstance(block, ToolResultBlock) and block.is_error:
                    metrics.tool_errors += 1
        return metrics

    async def list_sessions(self) -> list[str]:
        """列出 transcript 目录中的会话 id（仅 JsonlTranscriptStore）。"""
        if not isinstance(self.store, JsonlTranscriptStore):
            return []
        return sorted(p.stem for p in self.store.root.glob("*.jsonl"))
