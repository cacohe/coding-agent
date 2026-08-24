"""文件记忆：一条记忆一个 Markdown 文件 + MEMORY.md 索引。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["MemoryRecord", "FileMemoryStore", "memory_tools", "make_memory_extract_hook"]


@dataclass
class MemoryRecord:
    """单条记忆。"""

    name: str
    description: str
    mem_type: str
    body: str
    path: Path


class FileMemoryStore:
    """实现 ContextAssembler 的 MemoryPort.select_for_prompt。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "MEMORY.md"
        self.records: dict[str, MemoryRecord] = {}
        self.reload()

    def reload(self) -> None:
        """从磁盘重载全部记忆文件。"""
        self.records.clear()
        for path in sorted(self.root.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            record = _read_memory_file(path)
            if record:
                self.records[record.name] = record
        self._rebuild_index()

    def write(
        self,
        name: str,
        *,
        description: str,
        body: str,
        mem_type: str = "project",
    ) -> Path:
        """写入/覆盖一条记忆并刷新索引。"""
        slug = _slug(name)
        path = self.root / f"{slug}.md"
        path.write_text(
            _format_memory(name, description, mem_type, body),
            encoding="utf-8",
        )
        self.reload()
        return path

    async def select_for_prompt(self, query: str, *, limit: int = 5) -> str:
        """启发式关键词选择相关记忆。"""
        if not self.records:
            return ""
        q = query.lower()
        scored: list[tuple[int, MemoryRecord]] = []
        for record in self.records.values():
            hay = f"{record.name} {record.description} {record.body}".lower()
            score = sum(1 for token in _tokens(q) if token in hay)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = [r for _, r in scored[:limit]]
        if not chosen:
            return ""
        lines = [f"- {r.name} ({r.mem_type}): {r.description}" for r in chosen]
        return "\n".join(lines)

    def _rebuild_index(self) -> None:
        """重写 MEMORY.md 索引。"""
        lines = ["# Memory Index", ""]
        for record in self.records.values():
            lines.append(f"- [{record.name}]({record.path.name}): {record.description}")
        self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def memory_tools(store: FileMemoryStore):
    """导出工具工厂。"""
    from harness.memory.tools import memory_tools as _memory_tools

    return _memory_tools(store)


def make_memory_extract_hook(store: FileMemoryStore):
    """导出 Stop 抽取钩子工厂。"""
    from harness.memory.tools import make_memory_extract_hook as _make

    return _make(store)


def _slug(name: str) -> str:
    """生成文件名安全 slug。"""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-").lower()
    return s or "memory"


def _tokens(text: str) -> list[str]:
    """极简分词。"""
    return [t for t in re.split(r"\W+", text.lower()) if len(t) > 2]


def _format_memory(name: str, description: str, mem_type: str, body: str) -> str:
    """序列化为带 frontmatter 的 Markdown。"""
    return (
        f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body.strip()}\n"
    )


def _read_memory_file(path: Path) -> MemoryRecord | None:
    """解析记忆文件。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta: dict = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            import yaml

            try:
                loaded = yaml.safe_load(parts[1]) or {}
                if isinstance(loaded, dict):
                    meta = loaded
            except Exception:
                meta = {}
            body = parts[2]
    name = str(meta.get("name") or path.stem)
    return MemoryRecord(
        name=name,
        description=str(meta.get("description") or ""),
        mem_type=str(meta.get("type") or "project"),
        body=body.strip(),
        path=path,
    )
