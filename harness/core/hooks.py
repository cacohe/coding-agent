"""Hook 总线：在不改动 AgentLoop 的前提下挂扩展点。

对应 learn-claude-code 的 PreToolUse / PostToolUse / Stop 等事件，
生产版增加：优先级、超时、错误隔离、明确的 HookOutcome 语义。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    """循环生命周期中可订阅的事件名。"""

    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    BEFORE_MODEL = "BeforeModel"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    AFTER_MODEL = "AfterModel"
    STOP = "Stop"
    SESSION_END = "SessionEnd"


class HookAction(str, Enum):
    """钩子对当前阶段的干预方式。"""

    CONTINUE = "continue"  # 放行
    DENY = "deny"  # 拒绝（主要用于 PreToolUse）
    REWRITE = "rewrite"  # 改写 payload（如改 tool input）
    INJECT = "inject"  # 向 messages 注入额外内容
    FORCE_CONTINUE = "force_continue"  # Stop 时强制再跑一轮


class HookOutcome(BaseModel):
    """单个钩子（或总线聚合后）的返回值。"""

    action: HookAction = HookAction.CONTINUE
    reason: str = ""
    # REWRITE / INJECT / FORCE_CONTINUE 携带的载荷
    payload: Any = None

    @property
    def denied(self) -> bool:
        """是否应阻断工具执行。"""
        return self.action == HookAction.DENY


# 钩子回调：接收可变上下文字典，返回 HookOutcome 或 None（等同 continue）
HookHandler = Callable[[dict[str, Any]], Awaitable[HookOutcome | None] | HookOutcome | None]


class HookRegistration(BaseModel):
    """一次钩子注册的元数据。"""

    event: HookEvent
    name: str
    # 数值越小越先执行；同优先级按注册顺序
    priority: int = 100
    # 单钩子超时（秒）；超时记错误并视为 continue，避免拖死循环
    timeout_seconds: float = 5.0
    model_config = {"arbitrary_types_allowed": True}


class _HookEntry(BaseModel):
    """内部存储：元数据 + 可调用对象。"""

    registration: HookRegistration
    handler: Any  # HookHandler；Pydantic 不序列化调用方
    model_config = {"arbitrary_types_allowed": True}


class HookBus:
    """有序、可超时、错误隔离的钩子总线。"""

    def __init__(self) -> None:
        # 按事件分桶存放注册项
        self._hooks: dict[HookEvent, list[_HookEntry]] = {e: [] for e in HookEvent}

    def register(
        self,
        event: HookEvent,
        handler: HookHandler,
        *,
        name: str,
        priority: int = 100,
        timeout_seconds: float = 5.0,
    ) -> None:
        """注册钩子；同名同事件会覆盖旧处理器。"""
        entries = self._hooks[event]
        # 覆盖同名注册，便于 pack 热更新策略
        entries[:] = [e for e in entries if e.registration.name != name]
        entries.append(
            _HookEntry(
                registration=HookRegistration(
                    event=event,
                    name=name,
                    priority=priority,
                    timeout_seconds=timeout_seconds,
                ),
                handler=handler,
            )
        )
        # 保持优先级稳定排序
        entries.sort(key=lambda e: e.registration.priority)

    def register_many(self, items: list[tuple[HookEvent, HookHandler, str, int]]) -> None:
        """批量注册：(event, handler, name, priority)。"""
        for event, handler, name, priority in items:
            self.register(event, handler, name=name, priority=priority)

    async def emit(self, event: HookEvent, ctx: dict[str, Any]) -> HookOutcome:
        """按优先级触发钩子。

        聚合规则：
        - 任一 DENY：立即返回 deny（短路）
        - FORCE_CONTINUE / INJECT / REWRITE：取第一个非 continue 的干预
        - 全部 continue 或失败：返回 continue
        """
        for entry in self._hooks.get(event, []):
            reg = entry.registration
            try:
                result = entry.handler(ctx)
                # 兼容同步钩子
                if asyncio.iscoroutine(result):
                    result = await asyncio.wait_for(result, timeout=reg.timeout_seconds)
            except TimeoutError:
                logger.warning("hook %s timed out on %s", reg.name, event.value)
                continue
            except Exception:
                # 钩子故障不得拖垮主循环
                logger.exception("hook %s failed on %s", reg.name, event.value)
                continue

            if result is None:
                continue
            if not isinstance(result, HookOutcome):
                logger.warning("hook %s returned invalid type %s", reg.name, type(result))
                continue
            if result.action != HookAction.CONTINUE:
                return result
        return HookOutcome(action=HookAction.CONTINUE)
