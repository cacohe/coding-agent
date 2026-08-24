"""内置原子工具：在 workspace jail 内提供 read/write/edit/glob/exec。"""

from __future__ import annotations

import logging
from pathlib import Path

from harness.core.registry import Tool, ToolRegistry
from harness.core.types import RiskLevel, ToolResult, ToolSpec
from harness.tools.exec import run_exec, set_default_sandbox
from harness.tools.fs import edit_file, glob_files, read_file, write_file
from harness.tools.sandbox import SandboxBackend, detect_sandbox
from harness.tools.workspace import WorkspaceGuard

logger = logging.getLogger(__name__)


def register_builtin_tools(
    registry: ToolRegistry,
    workspace: Path,
    *,
    sandbox: SandboxBackend | None = None,
    sandbox_preference: str = "auto",
) -> WorkspaceGuard:
    """把内置工具注册进 ToolRegistry，返回 WorkspaceGuard 供其它组件复用。

    sandbox / sandbox_preference：配置 exec 使用的隔离后端。
    """
    guard = WorkspaceGuard(workspace)
    backend = sandbox or detect_sandbox(sandbox_preference)
    set_default_sandbox(backend)

    async def _read(path: str, limit: int | None = None) -> ToolResult:
        return read_file(guard, path, limit=limit)

    async def _write(path: str, content: str) -> ToolResult:
        return write_file(guard, path, content)

    async def _edit(path: str, old_text: str, new_text: str) -> ToolResult:
        return edit_file(guard, path, old_text, new_text)

    async def _glob(pattern: str) -> ToolResult:
        return glob_files(guard, pattern)

    async def _exec(command: list[str], timeout_seconds: int = 60) -> ToolResult:
        # 注意：接收 argv 列表，禁止隐式 shell=True
        return await run_exec(guard, command, timeout_seconds=timeout_seconds)

    tools = [
        Tool(
            spec=ToolSpec(
                name="read_file",
                description="Read a text file inside the workspace.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer", "description": "optional max lines"},
                    },
                    "required": ["path"],
                },
                risk=RiskLevel.READ,
            ),
            handler=_read,
        ),
        Tool(
            spec=ToolSpec(
                name="write_file",
                description="Write a text file inside the workspace (create or overwrite).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=_write,
        ),
        Tool(
            spec=ToolSpec(
                name="edit_file",
                description="Replace the first occurrence of old_text with new_text in a file.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=_edit,
        ),
        Tool(
            spec=ToolSpec(
                name="glob",
                description="List files matching a glob pattern under the workspace.",
                input_schema={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
                risk=RiskLevel.READ,
            ),
            handler=_glob,
        ),
        Tool(
            spec=ToolSpec(
                name="exec",
                description=(
                    'Run a command as argv list (no shell). Example: {"command": ["ls", "-la"]}'
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": 'argv list, e.g. ["python", "-V"]',
                        },
                        "timeout_seconds": {"type": "integer", "default": 60},
                    },
                    "required": ["command"],
                },
                risk=RiskLevel.EXEC,
            ),
            handler=_exec,
        ),
    ]
    registry.extend(tools)
    logger.info("registered %d builtin tools for workspace %s", len(tools), workspace)
    return guard
