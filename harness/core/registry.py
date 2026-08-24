"""工具注册表与分发器。

循环只认 ToolPool 里的 Tool；不关心工具来自内置、pack 还是 MCP。
一个工具 = schema（ToolSpec）+ 执行（handler）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from harness.core.types import RiskLevel, ToolResult, ToolSpec, ToolUseBlock

logger = logging.getLogger(__name__)

# 工具处理器：接收模型给出的 kwargs，返回 ToolResult
ToolHandler = Callable[..., Awaitable[ToolResult] | ToolResult]


@dataclass
class Tool:
    """唯一注册单元：给模型的 schema + 可调用实现。"""

    spec: ToolSpec
    handler: ToolHandler

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def risk(self) -> RiskLevel:
        return self.spec.risk

    async def execute(self, arguments: dict[str, Any] | None = None) -> ToolResult:
        """执行一次调用；异常规范为 is_error 结果。"""
        args = arguments or {}
        try:
            result = self.handler(**args)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, ToolResult):
                return result
            return ToolResult(content=str(result))
        except TypeError as exc:
            logger.warning("tool %s bad args: %s", self.name, exc)
            return ToolResult(content=f"Error: invalid arguments: {exc}", is_error=True)
        except Exception as exc:
            logger.exception("tool %s failed", self.name)
            return ToolResult(content=f"Error: {exc}", is_error=True)


@dataclass
class ToolPool:
    """某一时刻组装好的、可发给模型的工具集合。"""

    tools: dict[str, Tool] = field(default_factory=dict)

    def specs(self) -> list[ToolSpec]:
        """供 ModelPort 使用的 schema 列表。"""
        return [self.tools[name].spec for name in sorted(self.tools)]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)


class ToolRegistry:
    """可变的工具注册中心；assemble() 产出不可变快照式 ToolPool。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册或覆盖同名工具。"""
        self._tools[tool.name] = tool
        logger.debug("registered tool %s risk=%s", tool.name, tool.risk.value)

    def unregister(self, name: str) -> None:
        """移除工具（例如 MCP 断开时清理命名空间）。"""
        self._tools.pop(name, None)

    def extend(self, tools: Iterable[Tool]) -> None:
        """批量注册。"""
        for tool in tools:
            self.register(tool)

    def assemble(self) -> ToolPool:
        """组装当前全部工具为一次模型调用可用的池。"""
        return ToolPool(tools=dict(self._tools))

    def get(self, name: str) -> Tool | None:
        """按名查找。"""
        return self._tools.get(name)


class ToolDispatcher:
    """执行单个 tool_use：查找 Tool、未知工具返回 is_error。"""

    def __init__(self, pool: ToolPool) -> None:
        self.pool = pool

    async def execute(self, call: ToolUseBlock) -> ToolResult:
        """执行一次工具调用。"""
        tool = self.pool.get(call.name)
        if tool is None:
            return ToolResult(
                content=f"Error: unknown tool {call.name!r}",
                is_error=True,
            )
        return await tool.execute(call.input)
