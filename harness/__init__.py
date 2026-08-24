"""agent-harness：Agent Harness 运行时。

模型负责决策；本包负责工具执行、权限、上下文、记忆、任务、MCP、团队等环境能力。
模块低耦合；扩展通过 CapabilityPack，而不是改循环内核。
"""

from harness.api import Harness, Session
from harness.config import HarnessConfig
from harness.core.pack import CapabilityPack
from harness.core.permission import AutoApprove, StdinApproval
from harness.core.registry import Tool
from harness.core.types import (
    ContentBlock,
    Message,
    ModelResponse,
    StopDecision,
    ToolResult,
    ToolSpec,
    TurnResult,
)

__all__ = [
    "Harness",
    "Session",
    "HarnessConfig",
    "CapabilityPack",
    "Tool",
    "AutoApprove",
    "StdinApproval",
    "ContentBlock",
    "Message",
    "ModelResponse",
    "StopDecision",
    "ToolResult",
    "ToolSpec",
    "TurnResult",
]

__version__ = "0.1.0"
