"""Cron 调度器：按间隔把提示注入通知队列。

对齐 LCC s12：到点产生通知，由 NotificationHub 在下一轮 LLM 前注入。
使用单调时钟 + 线程，避免阻塞 AgentLoop。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CronJob:
    """一条定时任务。"""

    id: str
    # 注入给模型的提示文本
    prompt: str
    # 间隔秒数
    interval_seconds: float
    # 是否只触发一次
    once: bool = False
    # 下次触发的单调时钟时间戳
    next_fire: float = 0.0
    enabled: bool = True


class CronScheduler:
    """简单间隔型 cron（非完整 crontab 表达式；够用且可测）。"""

    def __init__(self) -> None:
        self._jobs: dict[str, CronJob] = {}
        self._notifications: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动后台 tick 线程（幂等）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="harness-cron", daemon=True)
        self._thread.start()
        logger.info("cron scheduler started")

    def stop(self) -> None:
        """停止 tick 线程。"""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def schedule(
        self,
        prompt: str,
        *,
        interval_seconds: float,
        once: bool = False,
        job_id: str | None = None,
        fire_immediately: bool = False,
    ) -> CronJob:
        """登记任务；默认在 interval 后首次触发。"""
        now = time.monotonic()
        job = CronJob(
            id=job_id or f"cron_{uuid.uuid4().hex[:8]}",
            prompt=prompt,
            interval_seconds=max(0.05, float(interval_seconds)),
            once=once,
            next_fire=now if fire_immediately else now + max(0.05, float(interval_seconds)),
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def cancel(self, job_id: str) -> bool:
        """取消任务。"""
        with self._lock:
            return self._jobs.pop(job_id, None) is not None

    def list_jobs(self) -> list[CronJob]:
        """列出当前任务快照。"""
        with self._lock:
            return list(self._jobs.values())

    def drain(self) -> list[str]:
        """取出到期通知（NotificationSource 协议）。"""
        with self._lock:
            notes = list(self._notifications)
            self._notifications.clear()
            return notes

    def tick(self, now: float | None = None) -> int:
        """单次推进（测试可手动调用，无需启动线程）。"""
        now = time.monotonic() if now is None else now
        fired = 0
        with self._lock:
            finished: list[str] = []
            for job in self._jobs.values():
                if not job.enabled or now < job.next_fire:
                    continue
                note = f"<cron_job id={job.id!r}>{job.prompt}</cron_job>"
                self._notifications.append(note)
                fired += 1
                if job.once:
                    finished.append(job.id)
                else:
                    job.next_fire = now + job.interval_seconds
            for jid in finished:
                self._jobs.pop(jid, None)
        return fired

    def _loop(self) -> None:
        """后台循环：每 50ms tick 一次。"""
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("cron tick failed")
            self._stop.wait(0.05)
