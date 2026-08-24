"""会话 transcript 持久化。

每次消息追加写入 JSONL，便于审计、回放与评测。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

from harness.core.types import Message

logger = logging.getLogger(__name__)


class TranscriptStore(ABC):
    """会话记录存储端口。"""

    @abstractmethod
    async def append(self, session_id: str, message: Message) -> None:
        """追加一条消息。"""

    @abstractmethod
    async def load(self, session_id: str) -> list[Message]:
        """加载完整会话（按写入顺序）。"""


class InMemoryTranscriptStore(TranscriptStore):
    """内存实现，供测试使用。"""

    def __init__(self) -> None:
        self._data: dict[str, list[Message]] = {}

    async def append(self, session_id: str, message: Message) -> None:
        self._data.setdefault(session_id, []).append(message)

    async def load(self, session_id: str) -> list[Message]:
        return list(self._data.get(session_id, []))


class JsonlTranscriptStore(TranscriptStore):
    """按会话拆分的 JSONL 文件存储。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        # 简单消毒，避免路径穿越
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        return self.root / f"{safe}.jsonl"

    async def append(self, session_id: str, message: Message) -> None:
        path = self._path(session_id)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "message": message.model_dump(),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def load(self, session_id: str) -> list[Message]:
        path = self._path(session_id)
        if not path.exists():
            return []
        messages: list[Message] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                messages.append(Message.model_validate(payload["message"]))
        return messages
