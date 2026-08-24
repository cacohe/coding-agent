"""无 shell 的 argv 执行器（可挂沙箱后端）。"""

from __future__ import annotations

import logging
from pathlib import Path

from harness.core.types import ToolResult
from harness.tools.sandbox import DirectSandbox, SandboxBackend
from harness.tools.workspace import WorkspaceGuard

logger = logging.getLogger(__name__)

# 绝对禁止的可执行名（basename 匹配）
_DENIED_BINARIES = {
    "shutdown",
    "reboot",
    "poweroff",
    "mkfs",
    "dd",
}

# 模块级默认沙箱；可由 register_builtin_tools / Harness 覆盖
_DEFAULT_SANDBOX: SandboxBackend = DirectSandbox()


def set_default_sandbox(sandbox: SandboxBackend) -> None:
    """设置全局默认沙箱后端。"""
    global _DEFAULT_SANDBOX
    _DEFAULT_SANDBOX = sandbox
    logger.info("default sandbox set to %s", sandbox.name)


def get_default_sandbox() -> SandboxBackend:
    """读取当前默认沙箱。"""
    return _DEFAULT_SANDBOX


async def run_exec(
    guard: WorkspaceGuard,
    command: list[str],
    *,
    timeout_seconds: int = 60,
    sandbox: SandboxBackend | None = None,
) -> ToolResult:
    """在 workspace cwd 下经沙箱执行 argv；捕获 stdout/stderr。"""
    if not command or not isinstance(command, list):
        return ToolResult(
            content="Error: command must be a non-empty list of strings", is_error=True
        )
    if not all(isinstance(c, str) for c in command):
        return ToolResult(content="Error: command argv must be strings", is_error=True)

    binary = Path(command[0]).name
    if binary in _DENIED_BINARIES:
        return ToolResult(content=f"Error: binary denied: {binary}", is_error=True)

    backend = sandbox or _DEFAULT_SANDBOX
    try:
        result = await backend.run(command, cwd=guard.root, timeout_seconds=timeout_seconds)
        combined = (result.stdout + result.stderr).strip()
        if len(combined) > 50_000:
            combined = combined[:50_000] + "\n...[truncated]"
        if result.exit_code != 0:
            return ToolResult(
                content=combined or f"(exit {result.exit_code}, no output)",
                is_error=True,
                artifacts={
                    "exit_code": result.exit_code,
                    "sandbox": result.backend,
                },
            )
        return ToolResult(
            content=combined or "(no output)",
            artifacts={"exit_code": result.exit_code, "sandbox": result.backend},
        )
    except FileNotFoundError:
        return ToolResult(content=f"Error: executable not found: {command[0]}", is_error=True)
    except Exception as exc:
        logger.exception("exec failed via sandbox=%s", backend.name)
        return ToolResult(content=f"Error: {exc}", is_error=True)
