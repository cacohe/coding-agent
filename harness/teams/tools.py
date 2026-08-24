"""把 TeamRuntime 暴露为 lead agent 可调用的工具。"""

from __future__ import annotations

import json

from harness.core.registry import Tool
from harness.core.types import RiskLevel, ToolResult, ToolSpec
from harness.teams.runtime import TeamRuntime


def team_tools(team: TeamRuntime) -> list[Tool]:
    """spawn_teammate / assign_teammate / wait_teammate / shutdown_teammate。"""

    async def spawn_teammate(name: str) -> ToolResult:
        try:
            handle = team.spawn(name)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        # 等 ready 报到
        results = team.wait_result(timeout=3.0)
        return ToolResult(
            content=json.dumps(
                {"name": handle.name, "pid": handle.process.pid, "ready": results},
                ensure_ascii=False,
            ),
            artifacts={"teammate": name, "pid": handle.process.pid},
        )

    async def assign_teammate(name: str, text: str) -> ToolResult:
        if name not in team.teammates:
            return ToolResult(content=f"Error: unknown teammate {name}", is_error=True)
        team.assign(name, text)
        return ToolResult(content=f"Assigned task to {name}")

    async def wait_teammate(timeout_seconds: float = 5.0) -> ToolResult:
        payloads = team.wait_result(timeout=timeout_seconds)
        if not payloads:
            return ToolResult(content="(no teammate results yet)")
        return ToolResult(content=json.dumps(payloads, ensure_ascii=False))

    async def shutdown_teammate(name: str) -> ToolResult:
        team.shutdown(name)
        return ToolResult(content=f"Shutdown requested for {name}")

    return [
        Tool(
            spec=ToolSpec(
                name="spawn_teammate",
                description="Start a process-isolated teammate that communicates via mailbox.",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                risk=RiskLevel.EXEC,
            ),
            handler=spawn_teammate,
        ),
        Tool(
            spec=ToolSpec(
                name="assign_teammate",
                description="Send a task message to a running teammate.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["name", "text"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=assign_teammate,
        ),
        Tool(
            spec=ToolSpec(
                name="wait_teammate",
                description="Wait for teammate result messages on the lead inbox.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "timeout_seconds": {"type": "number", "default": 5.0},
                    },
                },
                risk=RiskLevel.READ,
            ),
            handler=wait_teammate,
        ),
        Tool(
            spec=ToolSpec(
                name="shutdown_teammate",
                description="Ask a teammate to shut down gracefully.",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                risk=RiskLevel.EXEC,
            ),
            handler=shutdown_teammate,
        ),
    ]
