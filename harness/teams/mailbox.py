"""跨进程邮箱：SQLite 持久化，替代 LCC 的线程 + JSONL。

协议消息类型（字符串）：
- task       普通任务指派
- result     队友回传结果
- shutdown   请求队友退出
- ping / pong 探活
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MailMessage:
    """一封邮箱消息。"""

    id: str
    to_agent: str
    from_agent: str
    msg_type: str
    payload: dict[str, Any]
    created_at: float
    read: bool = False


class Mailbox:
    """基于 SQLite 的跨进程邮箱。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 进程内锁；跨进程靠 SQLite 事务
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        # 提升跨进程并发可读性
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mail (
                    id TEXT PRIMARY KEY,
                    to_agent TEXT NOT NULL,
                    from_agent TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    read INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mail_inbox ON mail(to_agent, read, created_at)"
            )
            conn.commit()

    def send(
        self,
        to_agent: str,
        from_agent: str,
        msg_type: str,
        payload: dict[str, Any] | None = None,
    ) -> MailMessage:
        """发送一封消息。"""
        msg = MailMessage(
            id=f"mail_{uuid.uuid4().hex[:12]}",
            to_agent=to_agent,
            from_agent=from_agent,
            msg_type=msg_type,
            payload=dict(payload or {}),
            created_at=time.time(),
            read=False,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO mail (id, to_agent, from_agent, msg_type, payload, created_at, read) "
                "VALUES (?,?,?,?,?,?,0)",
                (
                    msg.id,
                    msg.to_agent,
                    msg.from_agent,
                    msg.msg_type,
                    json.dumps(msg.payload, ensure_ascii=False),
                    msg.created_at,
                ),
            )
            conn.commit()
        return msg

    def read_inbox(self, agent: str, *, destructive: bool = True) -> list[MailMessage]:
        """读取未读收件箱；destructive=True 时标记已读。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mail WHERE to_agent=? AND read=0 ORDER BY created_at",
                (agent,),
            ).fetchall()
            messages = [_row_to_msg(r) for r in rows]
            if destructive and messages:
                ids = [m.id for m in messages]
                conn.executemany(
                    "UPDATE mail SET read=1 WHERE id=?",
                    [(i,) for i in ids],
                )
                conn.commit()
        return messages

    def wait_for(
        self,
        agent: str,
        *,
        timeout: float = 5.0,
        poll_interval: float = 0.05,
        msg_type: str | None = None,
    ) -> list[MailMessage]:
        """轮询直到收到消息或超时。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msgs = self.read_inbox(agent, destructive=True)
            if msg_type is not None:
                msgs = [m for m in msgs if m.msg_type == msg_type]
            if msgs:
                return msgs
            time.sleep(poll_interval)
        return []


def _row_to_msg(row: sqlite3.Row) -> MailMessage:
    """SQLite row → MailMessage。"""
    return MailMessage(
        id=row["id"],
        to_agent=row["to_agent"],
        from_agent=row["from_agent"],
        msg_type=row["msg_type"],
        payload=json.loads(row["payload"] or "{}"),
        created_at=float(row["created_at"]),
        read=bool(row["read"]),
    )
