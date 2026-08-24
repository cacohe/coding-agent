"""上下文组装与压缩。

ContextAssembler：skills 目录 / memory 摘要 / pack 片段 → system prompt。
Compactor：在调用模型前按 token 预算裁剪历史（替换 LCC 字符启发式）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

from harness.core.types import Message, TextBlock, ToolResultBlock

if TYPE_CHECKING:
    from harness.core.session import SessionState

logger = logging.getLogger(__name__)


class SkillCatalogPort(Protocol):
    """技能目录端口（由 harness-skills 实现）。"""

    def catalog_text(self) -> str:
        """返回可放入 system 的短目录。"""
        ...


class MemoryPort(Protocol):
    """记忆端口（由 harness-memory 实现）。"""

    async def select_for_prompt(self, query: str, *, limit: int = 5) -> str:
        """按当前用户问题挑选相关记忆，返回可嵌入 system 的文本。"""
        ...


class ContextAssembler:
    """拼装 system prompt：基础人设 + pack 片段 + skills 目录 + 记忆。"""

    def __init__(
        self,
        *,
        base_system: str,
        skill_catalog: SkillCatalogPort | None = None,
        memory: MemoryPort | None = None,
        pack_fragments: list[str] | None = None,
    ) -> None:
        self.base_system = base_system
        self.skill_catalog = skill_catalog
        self.memory = memory
        self.pack_fragments = list(pack_fragments or [])

    def add_fragment(self, text: str) -> None:
        """追加能力包提供的短系统片段（禁止塞长文档）。"""
        if text.strip():
            self.pack_fragments.append(text.strip())

    async def build(self, session: SessionState, *, latest_user_text: str = "") -> str:
        """生成本轮 system 字符串。"""
        parts: list[str] = [self.base_system.strip()]
        parts.extend(self.pack_fragments)

        if self.skill_catalog is not None:
            catalog = self.skill_catalog.catalog_text().strip()
            if catalog:
                parts.append("## Available skills\n" + catalog)
                parts.append(
                    "Use the load_skill tool to read a skill's full instructions when needed."
                )

        if self.memory is not None and latest_user_text:
            recalled = (await self.memory.select_for_prompt(latest_user_text)).strip()
            if recalled:
                parts.append("## Relevant memories\n" + recalled)

        # 工作区提示帮助模型约束路径
        if session.workspace:
            parts.append(f"Workspace root: {session.workspace}")

        return "\n\n".join(p for p in parts if p)


class Compactor(ABC):
    """上下文压缩策略接口。"""

    @abstractmethod
    async def prepare(self, messages: list[Message]) -> list[Message]:
        """返回可能被裁剪/摘要后的消息列表（可原地策略，但应返回列表）。"""


class NullCompactor(Compactor):
    """不做任何压缩；用于短会话与测试。"""

    async def prepare(self, messages: list[Message]) -> list[Message]:
        return messages


def estimate_tokens(text: str) -> int:
    """粗略 token 估计：约 4 字符/token。

    生产环境可替换为 tiktoken；此处避免强制重依赖。
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _message_tokens(message: Message) -> int:
    """估算单条消息占用。"""
    return estimate_tokens(message.text())


class BudgetCompactor(Compactor):
    """基于预算的压缩器。

    策略顺序（对齐 LCC s08）：
    1. 截断过长的 tool_result
    2. 丢弃过旧的中间 tool_result 正文（保留最近 N 条完整）
    3. 若仍超限，丢弃最早的非最近用户/助手轮次，插入摘要占位
    """

    def __init__(
        self,
        *,
        max_tokens: int = 50_000,
        max_tool_result_chars: int = 12_000,
        keep_recent_tool_results: int = 3,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_tool_result_chars = max_tool_result_chars
        self.keep_recent_tool_results = keep_recent_tool_results

    async def prepare(self, messages: list[Message]) -> list[Message]:
        # 先做 tool_result 单条截断
        trimmed = [self._trim_message(m) for m in messages]
        trimmed = self._micro_compact_tool_results(trimmed)
        total = sum(_message_tokens(m) for m in trimmed)
        if total <= self.max_tokens:
            return trimmed
        # 仍超限：保留尾部，头部折叠为摘要
        return self._fold_head(trimmed)

    def _trim_message(self, message: Message) -> Message:
        """截断过长 tool_result。"""
        if isinstance(message.content, str):
            return message
        new_blocks = []
        for block in message.content:
            if (
                isinstance(block, ToolResultBlock)
                and len(block.content) > self.max_tool_result_chars
            ):
                clipped = block.content[: self.max_tool_result_chars] + "\n...[truncated]"
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=clipped,
                        is_error=block.is_error,
                    )
                )
            else:
                new_blocks.append(block)
        return Message(role=message.role, content=new_blocks)

    def _micro_compact_tool_results(self, messages: list[Message]) -> list[Message]:
        """把较早的 tool_result 正文替换为占位，保留最近若干条。"""
        # 收集所有 tool_result 位置
        positions: list[tuple[int, int]] = []
        for mi, msg in enumerate(messages):
            if isinstance(msg.content, str):
                continue
            for bi, block in enumerate(msg.content):
                if isinstance(block, ToolResultBlock):
                    positions.append((mi, bi))
        drop = max(0, len(positions) - self.keep_recent_tool_results)
        to_drop = set(positions[:drop])
        if not to_drop:
            return messages

        out: list[Message] = []
        for mi, msg in enumerate(messages):
            if isinstance(msg.content, str):
                out.append(msg)
                continue
            new_blocks = []
            for bi, block in enumerate(msg.content):
                if (mi, bi) in to_drop and isinstance(block, ToolResultBlock):
                    new_blocks.append(
                        ToolResultBlock(
                            tool_use_id=block.tool_use_id,
                            content="[tool_result cleared to free context]",
                            is_error=block.is_error,
                        )
                    )
                else:
                    new_blocks.append(block)
            out.append(Message(role=msg.role, content=new_blocks))
        return out

    def _fold_head(self, messages: list[Message]) -> list[Message]:
        """折叠头部历史为一条摘要消息。"""
        if len(messages) <= 4:
            return messages
        # 保留最后 4 条原始消息
        keep = messages[-4:]
        dropped = messages[:-4]
        summary = TextBlock(
            text=(
                "[context compact] Earlier turns were folded. "
                f"Dropped {len(dropped)} messages to stay within token budget."
            )
        )
        folded = Message(role="user", content=[summary])
        return [folded, *keep]
