"""可插拔执行沙箱后端。

DirectSandbox：当前默认（仅 workspace cwd + 二进制黑名单）
BubblewrapSandbox：若系统有 bwrap，则用命名空间限制文件系统可见性
DockerSandbox：检测 docker 后可选启用（未装则跳过）

选择策略见 detect_sandbox()。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """沙箱执行结果。"""

    stdout: str
    stderr: str
    exit_code: int
    backend: str


class SandboxBackend(ABC):
    """执行沙箱端口。"""

    name: str = "base"

    @abstractmethod
    async def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = 60,
    ) -> SandboxResult:
        """在受限环境中执行 argv。"""


class DirectSandbox(SandboxBackend):
    """无额外隔离：直接 subprocess（仍由上层 WorkspaceGuard 约束 cwd）。"""

    name = "direct"

    async def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = 60,
    ) -> SandboxResult:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return SandboxResult(
                stdout="",
                stderr=f"Error: timeout after {timeout_seconds}s",
                exit_code=124,
                backend=self.name,
            )
        return SandboxResult(
            stdout=(out_b or b"").decode("utf-8", errors="replace"),
            stderr=(err_b or b"").decode("utf-8", errors="replace"),
            exit_code=int(proc.returncode or 0),
            backend=self.name,
        )


class BubblewrapSandbox(SandboxBackend):
    """使用 bubblewrap 限制可见文件系统。

    策略：只读系统路径 + 读写绑定 workspace；无 network（--unshare-net）。
    """

    name = "bubblewrap"

    def __init__(self, bwrap_bin: str = "bwrap") -> None:
        self.bwrap_bin = bwrap_bin

    async def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = 60,
    ) -> SandboxResult:
        root = cwd.resolve()
        # 构造 bwrap 参数：最小可用用户态沙箱
        bwrap_cmd = [
            self.bwrap_bin,
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-net",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind-try",
            "/lib64",
            "/lib64",
            "--ro-bind-try",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(root),
            str(root),
            "--chdir",
            str(root),
            "--",
            *command,
        ]
        # 若缺少某些路径，bwrap 可能失败；交给调用方看 stderr
        inner = DirectSandbox()
        # 注意：这里用 host 的 DirectSandbox 去跑 bwrap 本身
        result = await inner.run(bwrap_cmd, cwd=root, timeout_seconds=timeout_seconds)
        return SandboxResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            backend=self.name,
        )


class DockerSandbox(SandboxBackend):
    """可选 Docker 后端：把 workspace 挂进临时容器执行。

    需要本机 docker 可用；镜像默认 python:3.12-slim。
    """

    name = "docker"

    def __init__(self, image: str = "python:3.12-slim", docker_bin: str = "docker") -> None:
        self.image = image
        self.docker_bin = docker_bin

    async def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = 60,
    ) -> SandboxResult:
        root = cwd.resolve()
        docker_cmd = [
            self.docker_bin,
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            self.image,
            *command,
        ]
        inner = DirectSandbox()
        result = await inner.run(docker_cmd, cwd=root, timeout_seconds=timeout_seconds)
        return SandboxResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            backend=self.name,
        )


def detect_sandbox(preference: str = "auto") -> SandboxBackend:
    """按偏好选择沙箱。

    preference: auto | direct | bubblewrap | docker
    auto：有 bwrap 用 bwrap，否则 direct（不默认 docker，避免拉镜像副作用）
    """
    pref = (preference or "auto").lower()
    if pref == "direct":
        return DirectSandbox()
    if pref == "bubblewrap":
        path = shutil.which("bwrap")
        if not path:
            raise RuntimeError("bwrap not found")
        return BubblewrapSandbox(path)
    if pref == "docker":
        path = shutil.which("docker")
        if not path:
            raise RuntimeError("docker not found")
        return DockerSandbox(docker_bin=path)
    # auto
    bwrap = shutil.which("bwrap")
    if bwrap:
        logger.info("sandbox backend: bubblewrap")
        return BubblewrapSandbox(bwrap)
    logger.info("sandbox backend: direct")
    return DirectSandbox()
