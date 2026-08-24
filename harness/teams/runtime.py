"""进程级队友运行时。

每个队友是独立 multiprocessing.Process，通过 Mailbox 通信。
默认 worker 实现「回声/结果」协议，便于无 LLM 时测通协作；
也可传入自定义 target。
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.teams.mailbox import Mailbox

logger = logging.getLogger(__name__)


@dataclass
class TeammateHandle:
    """已启动队友的句柄。"""

    name: str
    process: mp.Process
    # 可选元数据（角色说明等）
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        """进程是否仍在运行。"""
        return self.process.is_alive()


def _default_worker(name: str, mailbox_path: str, lead: str = "lead") -> None:
    """默认队友主循环：处理 task → 回 result；收到 shutdown 退出。

    这是进程入口，必须是顶层可 pickle 函数。
    """
    mailbox = Mailbox(Path(mailbox_path))
    # 向 lead 报到
    mailbox.send(lead, name, "result", {"status": "ready", "agent": name})
    while True:
        inbox = mailbox.read_inbox(name, destructive=True)
        for msg in inbox:
            if msg.msg_type == "shutdown":
                mailbox.send(lead, name, "result", {"status": "shutdown", "agent": name})
                return
            if msg.msg_type == "ping":
                mailbox.send(msg.from_agent, name, "pong", {"agent": name})
                continue
            if msg.msg_type == "task":
                # 默认：把任务原文包装成完成结果（真实场景此处跑子 AgentLoop）
                task_text = str(msg.payload.get("text") or msg.payload.get("prompt") or "")
                mailbox.send(
                    msg.from_agent,
                    name,
                    "result",
                    {
                        "status": "done",
                        "agent": name,
                        "echo": task_text,
                        "summary": f"{name} completed: {task_text[:200]}",
                    },
                )
                continue
        time.sleep(0.05)


class TeamRuntime:
    """管理多个进程队友。"""

    def __init__(
        self,
        mailbox: Mailbox,
        *,
        lead_name: str = "lead",
        worker: Callable[..., None] | None = None,
    ) -> None:
        self.mailbox = mailbox
        self.lead_name = lead_name
        self.worker = worker or _default_worker
        self.teammates: dict[str, TeammateHandle] = {}

    def spawn(self, name: str, *, meta: dict[str, Any] | None = None) -> TeammateHandle:
        """启动一个命名队友进程。"""
        if name in self.teammates and self.teammates[name].alive:
            raise ValueError(f"teammate {name!r} already running")
        # spawn 上下文更跨平台；Linux 下也避免 fork 继承锁
        ctx = mp.get_context("spawn")
        proc = ctx.Process(
            target=self.worker,
            name=f"harness-teammate-{name}",
            args=(name, str(self.mailbox.db_path), self.lead_name),
            daemon=True,
        )
        proc.start()
        handle = TeammateHandle(name=name, process=proc, meta=dict(meta or {}))
        self.teammates[name] = handle
        logger.info("spawned teammate %s pid=%s", name, proc.pid)
        return handle

    def assign(self, teammate: str, text: str, *, from_agent: str | None = None) -> None:
        """向队友派发 task 消息。"""
        self.mailbox.send(
            teammate,
            from_agent or self.lead_name,
            "task",
            {"text": text},
        )

    def shutdown(self, teammate: str, *, timeout: float = 3.0) -> None:
        """请求队友优雅退出，超时则 terminate。"""
        self.mailbox.send(teammate, self.lead_name, "shutdown", {})
        handle = self.teammates.get(teammate)
        if handle is None:
            return
        handle.process.join(timeout=timeout)
        if handle.process.is_alive():
            handle.process.terminate()
            handle.process.join(timeout=1.0)

    def shutdown_all(self, *, timeout: float = 3.0) -> None:
        """关闭全部队友。"""
        for name in list(self.teammates):
            self.shutdown(name, timeout=timeout)

    def wait_result(self, *, timeout: float = 5.0) -> list[Any]:
        """等待 lead 收件箱中的 result 消息。"""
        msgs = self.mailbox.wait_for(self.lead_name, timeout=timeout, msg_type="result")
        return [m.payload for m in msgs]
