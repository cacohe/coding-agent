"""权限门控：在 PreToolUse 之后、真正执行之前裁决。

替换 LCC 的字符串黑名单 + stdin y/N：
- 基于 RiskLevel 与规则表
- Ask 通过 ApprovalPort 解耦 CLI / UI / 自动策略
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel

from harness.core.types import RiskLevel, ToolUseBlock

logger = logging.getLogger(__name__)


class PermissionDecision(str, Enum):
    """策略引擎对一次工具调用的结论。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionVerdict(BaseModel):
    """带原因的裁决，供循环写入 tool_result 或触发审批。"""

    decision: PermissionDecision
    reason: str = ""
    risk: RiskLevel = RiskLevel.READ


class PermissionRule(BaseModel):
    """单条权限规则。

    匹配优先级由 Policy 内的 rules 顺序决定（先匹配先生效）。
    """

    # 工具名 glob；"*" 匹配全部
    tool_pattern: str = "*"
    risk: RiskLevel | None = None  # 若设置，仅匹配该风险等级
    decision: PermissionDecision
    reason: str = ""


class ApprovalPort(ABC):
    """审批通道抽象：CLI、Web、自动批准均可实现。"""

    @abstractmethod
    async def request(self, *, tool: ToolUseBlock, verdict: PermissionVerdict) -> bool:
        """返回 True 表示用户/策略批准执行。"""


class AutoApprove(ApprovalPort):
    """测试与无交互环境使用的自动批准器。"""

    def __init__(self, approve: bool = True) -> None:
        self.approve = approve

    async def request(self, *, tool: ToolUseBlock, verdict: PermissionVerdict) -> bool:
        # 直接按构造参数返回，避免阻塞
        return self.approve


class StdinApproval(ApprovalPort):
    """在终端向用户请求 y/N（参考 CLI；运行时本身不强制使用）。"""

    async def request(self, *, tool: ToolUseBlock, verdict: PermissionVerdict) -> bool:
        prompt = (
            f"[permission] allow {tool.name} ({verdict.risk.value})? "
            f"{verdict.reason or tool.input} [y/N]: "
        )
        try:
            # 同步 input 包在默认 executor 外由 host 决定；此处保持简单
            answer = input(prompt)
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}


def _name_matches(pattern: str, name: str) -> bool:
    """极简 glob：支持 * 后缀/全匹配。"""
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return pattern == name


class PermissionGate:
    """规则表驱动的权限门。"""

    def __init__(
        self,
        rules: list[PermissionRule] | None = None,
        *,
        # 工具名 -> 风险；注册工具时写入
        risk_by_tool: dict[str, RiskLevel] | None = None,
        approval: ApprovalPort | None = None,
        # 默认决策：未命中任何规则时使用
        default_decision: PermissionDecision = PermissionDecision.ASK,
    ) -> None:
        self.rules = list(rules or [])
        self.risk_by_tool = dict(risk_by_tool or {})
        self.approval = approval or AutoApprove(True)
        self.default_decision = default_decision

    def set_tool_risk(self, name: str, risk: RiskLevel) -> None:
        """工具注册时同步风险元数据。"""
        self.risk_by_tool[name] = risk

    def add_rules(self, rules: list[PermissionRule]) -> None:
        """追加能力包带来的规则（插到列表前部，优先于默认）。"""
        self.rules = list(rules) + self.rules

    def evaluate(self, tool: ToolUseBlock) -> PermissionVerdict:
        """纯函数式裁决（不含 Ask 交互）。"""
        risk = self.risk_by_tool.get(tool.name, RiskLevel.READ)
        for rule in self.rules:
            if not _name_matches(rule.tool_pattern, tool.name):
                continue
            if rule.risk is not None and rule.risk != risk:
                continue
            return PermissionVerdict(
                decision=rule.decision,
                reason=rule.reason or f"matched rule for {tool.name}",
                risk=risk,
            )
        return PermissionVerdict(
            decision=self.default_decision,
            reason="no matching rule; using default",
            risk=risk,
        )

    async def authorize(self, tool: ToolUseBlock) -> PermissionVerdict:
        """完整授权流程：evaluate → 若 ASK 则走 ApprovalPort。"""
        verdict = self.evaluate(tool)
        if verdict.decision == PermissionDecision.ALLOW:
            return verdict
        if verdict.decision == PermissionDecision.DENY:
            logger.info("denied tool %s: %s", tool.name, verdict.reason)
            return verdict
        # ASK
        approved = await self.approval.request(tool=tool, verdict=verdict)
        if approved:
            return PermissionVerdict(
                decision=PermissionDecision.ALLOW,
                reason="approved by ApprovalPort",
                risk=verdict.risk,
            )
        return PermissionVerdict(
            decision=PermissionDecision.DENY,
            reason="rejected by ApprovalPort",
            risk=verdict.risk,
        )


def default_coding_rules() -> list[PermissionRule]:
    """编码场景的保守默认策略。"""
    return [
        PermissionRule(
            tool_pattern="*",
            risk=RiskLevel.READ,
            decision=PermissionDecision.ALLOW,
            reason="read tools are allowed by default",
        ),
        PermissionRule(
            tool_pattern="*",
            risk=RiskLevel.WRITE,
            decision=PermissionDecision.ASK,
            reason="writes require approval",
        ),
        PermissionRule(
            tool_pattern="*",
            risk=RiskLevel.EXEC,
            decision=PermissionDecision.ASK,
            reason="exec requires approval",
        ),
        PermissionRule(
            tool_pattern="*",
            risk=RiskLevel.NETWORK,
            decision=PermissionDecision.DENY,
            reason="network denied by default",
        ),
    ]
