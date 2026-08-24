"""MCP 网关：本地工具 + 可选真实 stdio 传输。

命名约定：mcp__{server}__{tool}
- 本地注册：始终可用，用于测试与无外部 mcp 包环境
- stdio：依赖官方 mcp 包，用 AsyncExitStack 保持长连接
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from harness.core.registry import Tool, ToolHandler, ToolRegistry
from harness.core.types import RiskLevel, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """单个 MCP server 的连接配置。"""

    name: str
    transport: str = "stdio"  # stdio | local
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class _LocalTool:
    """进程内 MCP 工具（无需真实传输）。"""

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    risk: RiskLevel = RiskLevel.NETWORK


class _StdioSession:
    """托管一条 stdio MCP 连接。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.session: Any = None
        self._stack: AsyncExitStack | None = None

    async def start(self) -> None:
        """启动子进程并完成 initialize 握手。"""
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters

        if not self.config.command:
            raise ValueError(f"stdio server {self.config.name} missing command")
        command = self.config.command[0]
        args = list(self.config.command[1:])
        params = StdioServerParameters(
            command=command,
            args=args,
            env=self.config.env or None,
        )
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack = stack
        self.session = session
        logger.info("mcp stdio connected: %s", self.config.name)

    async def close(self) -> None:
        """关闭连接与子进程。"""
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self.session = None


class MCPGateway:
    """管理 MCP 连接并向 ToolRegistry 注入命名空间工具。"""

    def __init__(self) -> None:
        self.servers: dict[str, MCPServerConfig] = {}
        self._local: list[_LocalTool] = []
        self._stdio: dict[str, _StdioSession] = {}
        self._remote_tools: dict[str, list[ToolSpec]] = {}

    def add_server(self, config: MCPServerConfig) -> None:
        """登记 server 配置（尚未真正连接）。"""
        self.servers[config.name] = config
        logger.info("mcp server registered: %s (%s)", config.name, config.transport)

    def register_local_tool(
        self,
        server: str,
        name: str,
        handler: ToolHandler,
        *,
        description: str,
        input_schema: dict[str, Any] | None = None,
        risk: RiskLevel = RiskLevel.NETWORK,
    ) -> None:
        """注册进程内工具，模拟 MCP 命名空间（无需官方 mcp 包）。"""
        self._local.append(
            _LocalTool(
                server=server,
                name=name,
                description=description,
                input_schema=input_schema
                or {"type": "object", "properties": {}, "additionalProperties": True},
                handler=handler,
                risk=risk,
            )
        )

    async def connect_all(self, registry: ToolRegistry) -> None:
        """连接配置的 server，并把本地 + 远程工具写入 registry。"""
        # 1) 始终挂载本地工具
        for tool in self._local:
            full = self.namespace(tool.server, tool.name)
            spec = ToolSpec(
                name=full,
                description=tool.description,
                input_schema=tool.input_schema,
                risk=tool.risk,
            )
            registry.register(Tool(spec=spec, handler=tool.handler))
            self._remote_tools.setdefault(tool.server, []).append(spec)

        # 2) stdio 真连接
        for name, cfg in self.servers.items():
            if cfg.transport == "local":
                continue
            if cfg.transport != "stdio":
                logger.warning("unsupported mcp transport %s for %s", cfg.transport, name)
                continue
            await self._connect_stdio(name, cfg, registry)

    async def _connect_stdio(self, name: str, cfg: MCPServerConfig, registry: ToolRegistry) -> None:
        """连接单个 stdio server 并 list_tools。"""
        try:
            import importlib.util

            if importlib.util.find_spec("mcp") is None:
                logger.warning("mcp package not installed; skipping stdio server %s", name)
                return
        except Exception:
            logger.warning("unable to probe mcp package; skipping %s", name)
            return

        try:
            conn = _StdioSession(cfg)
            await conn.start()
            self._stdio[name] = conn
            assert conn.session is not None
            listed = await conn.session.list_tools()
            specs: list[ToolSpec] = []
            for remote in listed.tools:
                full = self.namespace(name, remote.name)
                schema = (
                    getattr(remote, "inputSchema", None)
                    or getattr(remote, "input_schema", None)
                    or {"type": "object", "properties": {}}
                )
                spec = ToolSpec(
                    name=full,
                    description=remote.description or f"MCP tool {remote.name}",
                    input_schema=dict(schema),
                    risk=RiskLevel.NETWORK,
                )
                handler = self._make_remote_handler(name, remote.name)
                registry.register(Tool(spec=spec, handler=handler))
                specs.append(spec)
            self._remote_tools[name] = specs
            logger.info("imported %d tools from mcp server %s", len(specs), name)
        except Exception:
            logger.exception("failed to connect mcp server %s", name)

    def _make_remote_handler(self, server: str, tool_name: str) -> ToolHandler:
        """为远程工具生成闭包 handler。"""

        async def handler(**kwargs: Any) -> ToolResult:
            conn = self._stdio.get(server)
            if conn is None or conn.session is None:
                return ToolResult(
                    content=f"Error: mcp server {server} not connected",
                    is_error=True,
                )
            try:
                result = await conn.session.call_tool(tool_name, arguments=kwargs)
                texts: list[str] = []
                for block in result.content or []:
                    text = getattr(block, "text", None)
                    if text:
                        texts.append(text)
                    else:
                        texts.append(str(block))
                is_error = bool(
                    getattr(result, "isError", False) or getattr(result, "is_error", False)
                )
                return ToolResult(
                    content="\n".join(texts) if texts else "(empty mcp result)",
                    is_error=is_error,
                    artifacts={"server": server, "tool": tool_name},
                )
            except Exception as exc:
                logger.exception("mcp call failed %s/%s", server, tool_name)
                return ToolResult(content=f"Error: {exc}", is_error=True)

        return handler

    async def close(self) -> None:
        """关闭全部 stdio 连接。"""
        for name, conn in list(self._stdio.items()):
            try:
                await conn.close()
            except Exception:
                logger.exception("error closing mcp server %s", name)
        self._stdio.clear()

    def namespace(self, server: str, tool: str) -> str:
        """生成稳定工具名。"""
        return f"mcp__{server}__{tool}"

    def make_stub_tool(self, server: str, tool: str, description: str) -> Tool:
        """测试用 stub：注册后调用返回固定说明。"""

        full_name = self.namespace(server, tool)

        async def handler(**kwargs: Any) -> ToolResult:
            return ToolResult(
                content=f"MCP stub {full_name} called with {kwargs}",
                artifacts={"server": server, "tool": tool},
            )

        return Tool(
            spec=ToolSpec(
                name=full_name,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
                risk=RiskLevel.NETWORK,
            ),
            handler=handler,
        )
