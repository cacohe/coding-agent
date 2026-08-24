"""持久任务存储（SQLite）、后台作业与 Cron。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.tasks.cron import CronJob, CronScheduler

logger = logging.getLogger(__name__)

__all__ = [
    "TaskRecord",
    "TaskStore",
    "BackgroundJobRunner",
    "CronJob",
    "CronScheduler",
    "task_tools",
]


@dataclass
class TaskRecord:
    """任务记录。"""

    id: str
    subject: str
    description: str
    status: str  # pending | in_progress | completed | cancelled
    owner: str | None
    blocked_by: list[str]


class TaskStore:
    """基于 SQLite 的任务图。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner TEXT,
                    blocked_by TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
    ) -> TaskRecord:
        """创建 pending 任务。"""
        task = TaskRecord(
            id=f"task_{uuid.uuid4().hex[:8]}",
            subject=subject,
            description=description,
            status="pending",
            owner=None,
            blocked_by=list(blocked_by or []),
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO tasks (id, subject, description, status, owner, blocked_by) "
                "VALUES (?,?,?,?,?,?)",
                (
                    task.id,
                    task.subject,
                    task.description,
                    task.status,
                    task.owner,
                    json.dumps(task.blocked_by),
                ),
            )
            conn.commit()
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        """按 id 读取。"""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(self, status: str | None = None) -> list[TaskRecord]:
        """列出任务，可按状态过滤。"""
        with self._lock, self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY id", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [_row_to_task(r) for r in rows]

    def claim(self, task_id: str, owner: str) -> TaskRecord | None:
        """原子认领：仅 pending 且依赖已完成时可变为 in_progress。"""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            task = _row_to_task(row)
            if task.status != "pending":
                return None
            for dep in task.blocked_by:
                dep_row = conn.execute("SELECT status FROM tasks WHERE id=?", (dep,)).fetchone()
                if dep_row is None or dep_row["status"] != "completed":
                    return None
            conn.execute(
                "UPDATE tasks SET status=?, owner=? WHERE id=?",
                ("in_progress", owner, task_id),
            )
            conn.commit()
            task.status = "in_progress"
            task.owner = owner
            return task

    def complete(self, task_id: str) -> TaskRecord | None:
        """标记完成。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status=? WHERE id=?",
                ("completed", task_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None


def _row_to_task(row: sqlite3.Row) -> TaskRecord:
    """SQLite row → TaskRecord。"""
    return TaskRecord(
        id=row["id"],
        subject=row["subject"],
        description=row["description"],
        status=row["status"],
        owner=row["owner"],
        blocked_by=json.loads(row["blocked_by"] or "[]"),
    )


class BackgroundJobRunner:
    """后台作业：线程执行 callable，完成后投入通知队列。"""

    def __init__(self) -> None:
        self._notifications: list[str] = []
        self._lock = threading.Lock()

    def spawn(self, name: str, fn: Callable[[], Any]) -> None:
        """启动守护线程执行任务。"""

        def _run() -> None:
            try:
                result = fn()
                note = f"<task_notification name={name!r}>{result}</task_notification>"
            except Exception as exc:
                note = f"<task_notification name={name!r} error={exc!r}/>"
            with self._lock:
                self._notifications.append(str(note))

        thread = threading.Thread(target=_run, name=f"harness-bg-{name}", daemon=True)
        thread.start()

    def drain(self) -> list[str]:
        """取出并清空待注入通知。"""
        with self._lock:
            notes = list(self._notifications)
            self._notifications.clear()
            return notes


def task_tools(store: TaskStore):
    """延迟导入，避免循环依赖在类型检查时炸开。"""
    from harness.tasks.tools import task_tools as _task_tools

    return _task_tools(store)
