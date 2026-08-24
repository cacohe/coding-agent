"""Stop 策略：在模型不再调用工具时决定是否真正结束。

对齐 LCC s17 GoalGate；默认实现为「无 tool_use 即允许停止」。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from harness.core.types import ModelResponse, StopAction, StopDecision

if TYPE_CHECKING:
    from harness.core.session import SessionState


class StopPolicy(ABC):
    """Stop 边界策略接口。"""

    @abstractmethod
    async def decide(
        self,
        session: SessionState,
        response: ModelResponse,
    ) -> StopDecision:
        """在模型返回无 tool_use 的响应后调用。"""


class AllowStopPolicy(StopPolicy):
    """默认策略：模型停就停。"""

    async def decide(
        self,
        session: SessionState,
        response: ModelResponse,
    ) -> StopDecision:
        return StopDecision(action=StopAction.ALLOW, reason="model returned no tool_use")


class MaxIterationStopPolicy(StopPolicy):
    """装饰型策略：超出最大工具轮次则强制 LIMIT。

    与内层 policy 组合使用时，先检查迭代再委托。
    """

    def __init__(self, inner: StopPolicy, *, max_iterations: int = 40) -> None:
        self.inner = inner
        self.max_iterations = max_iterations

    async def decide(
        self,
        session: SessionState,
        response: ModelResponse,
    ) -> StopDecision:
        if session.iteration_count >= self.max_iterations:
            return StopDecision(
                action=StopAction.LIMIT,
                reason=f"reached max_iterations={self.max_iterations}",
            )
        return await self.inner.decide(session, response)


class GoalStopPolicy(StopPolicy):
    """目标门控：会话 extras['goal'] 未满足时强制 CONTINUE。

    评估方式（可组合）：
    1. 若设置了 goal_checker 回调，以其返回为准
    2. 否则用关键词启发式：goal 中的实词是否出现在最终回复或近期消息中
    3. 模型回复若含明确失败标记则 FAIL

    对齐 s17：Stop 边界由独立策略决定，而不是改循环。
    """

    def __init__(
        self,
        inner: StopPolicy | None = None,
        *,
        max_continues: int = 5,
        fail_markers: tuple[str, ...] = ("GOAL_FAILED", "无法完成目标", "impossible"),
    ) -> None:
        self.inner = inner or AllowStopPolicy()
        self.max_continues = max_continues
        self.fail_markers = fail_markers

    async def decide(
        self,
        session: SessionState,
        response: ModelResponse,
    ) -> StopDecision:
        goal = str(session.extras.get("goal") or "").strip()
        if not goal:
            # 无目标时退回内层策略
            return await self.inner.decide(session, response)

        text = response.text or ""
        # 显式失败标记
        lower = text.lower()
        for marker in self.fail_markers:
            if marker.lower() in lower:
                return StopDecision(
                    action=StopAction.FAIL,
                    reason=f"goal marked failed via {marker!r}",
                )

        # 可选外部检查器：extras['goal_checker'] = Callable[[session, response], bool]
        checker = session.extras.get("goal_checker")
        achieved = False
        if callable(checker):
            try:
                achieved = bool(checker(session, response))
            except Exception:
                achieved = False
        else:
            achieved = _heuristic_goal_met(goal, text, session)

        if achieved:
            return StopDecision(action=StopAction.ALLOW, reason="goal achieved")

        # 续跑计数存在 extras，避免无限循环
        continues = int(session.extras.get("goal_continues") or 0)
        if continues >= self.max_continues:
            return StopDecision(
                action=StopAction.LIMIT,
                reason=f"goal not met after {continues} continuations",
            )
        session.extras["goal_continues"] = continues + 1
        return StopDecision(
            action=StopAction.CONTINUE,
            reason="goal not yet met",
            prompt=(
                f"The goal is not complete yet: {goal}\n"
                "Continue working. When done, summarize how the goal was met. "
                "If impossible, reply with GOAL_FAILED and why."
            ),
        )


def _heuristic_goal_met(goal: str, final_text: str, session: SessionState) -> bool:
    """极简启发式：goal 中长度>3 的词至少命中一半出现在最终回复里。

    生产环境应换成独立评估模型；此处保证无额外依赖即可测通。
    """
    tokens = [t for t in re.split(r"\W+", goal.lower()) if len(t) > 3]
    if not tokens:
        # 目标过短：若模型已给出非空最终答复则视为完成
        return bool(final_text.strip())
    hay = final_text.lower()
    # 也扫最近几条消息，覆盖「已写入文件」类隐式完成
    for msg in session.messages[-6:]:
        hay += "\n" + msg.text().lower()
    hits = sum(1 for t in tokens if t in hay)
    return hits >= max(1, (len(tokens) + 1) // 2)
