"""Agent loop 内核：类型、循环、权限、上下文、模型端口等。

门面入口为 `agent_harness` / `agent_harness.api`；
厂商模型适配在 `agent_harness.models`，不进入本子包。
"""

from harness.core.hooks import HookBus, HookEvent
from harness.core.loop import AgentLoop
from harness.core.model import ModelClient
from harness.core.pack import CapabilityPack
from harness.core.permission import AutoApprove, PermissionGate, StdinApproval
from harness.core.registry import Tool, ToolRegistry
from harness.core.session import Session, SessionState
from harness.core.types import Message, ToolResult, ToolSpec, TurnResult

__all__ = [
    "AgentLoop",
    "AutoApprove",
    "CapabilityPack",
    "HookBus",
    "HookEvent",
    "Message",
    "ModelClient",
    "PermissionGate",
    "Session",
    "SessionState",
    "StdinApproval",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "TurnResult",
]
