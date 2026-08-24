"""本地知识索引与检索工具。

可与 CapabilityPack 组合使用；本模块只提供索引与 register 辅助，不绑定业务产品。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from harness.core.permission import PermissionDecision, PermissionRule
from harness.core.registry import Tool
from harness.core.types import RiskLevel, ToolResult, ToolSpec

if TYPE_CHECKING:
    from harness.api import Harness


@dataclass
class Chunk:
    """文档分块。"""

    doc_id: str
    text: str
    index: int


@dataclass
class KnowledgeIndex:
    """内存知识索引：分块 + 关键词检索。"""

    chunks: list[Chunk] = field(default_factory=list)

    def add_document(
        self, doc_id: str, text: str, *, chunk_size: int = 500, overlap: int = 50
    ) -> int:
        """按字符窗口分块；返回新增块数。"""
        text = text.strip()
        if not text:
            return 0
        added = 0
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            piece = text[start:end].strip()
            if piece:
                self.chunks.append(Chunk(doc_id=doc_id, text=piece, index=len(self.chunks)))
                added += 1
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
        return added

    def ingest_dir(self, root: Path) -> int:
        """加载目录下 txt/md 文件，返回新增块数。"""
        root = Path(root)
        total = 0
        if not root.exists():
            return 0
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".txt", ".md", ".markdown"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            total += self.add_document(str(path.name), text)
        return total

    def search(self, query: str, *, top_k: int = 5) -> list[Chunk]:
        """关键词打分检索。"""
        tokens = [t for t in re.split(r"\W+", query.lower()) if len(t) > 1]
        if not tokens:
            return []
        scored: list[tuple[int, Chunk]] = []
        for chunk in self.chunks:
            hay = chunk.text.lower()
            score = sum(hay.count(t) for t in tokens)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]


def knowledge_tools(index: KnowledgeIndex) -> list[Tool]:
    """生成 knowledge_search / ingest_document 工具对。"""

    async def knowledge_search(query: str, top_k: int = 5) -> ToolResult:
        hits = index.search(query, top_k=top_k)
        if not hits:
            return ToolResult(content="(no matching chunks)")
        parts = [f"[{i}] ({c.doc_id}#{c.index})\n{c.text}" for i, c in enumerate(hits, 1)]
        return ToolResult(content="\n\n".join(parts))

    async def ingest_document(doc_id: str, content: str) -> ToolResult:
        n = index.add_document(doc_id, content)
        return ToolResult(content=f"Ingested {n} chunks for {doc_id}")

    return [
        Tool(
            spec=ToolSpec(
                name="knowledge_search",
                description="Search the local knowledge base for relevant chunks.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                risk=RiskLevel.READ,
            ),
            handler=knowledge_search,
        ),
        Tool(
            spec=ToolSpec(
                name="ingest_document",
                description="Add a text document into the knowledge index.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["doc_id", "content"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=ingest_document,
        ),
    ]


def knowledge_permission_rules() -> list[PermissionRule]:
    """知识工具默认权限：检索允许，写入需审批。"""
    return [
        PermissionRule(
            tool_pattern="knowledge_search",
            decision=PermissionDecision.ALLOW,
            reason="knowledge search is read-only",
        ),
        PermissionRule(
            tool_pattern="ingest_document",
            decision=PermissionDecision.ASK,
            reason="ingesting documents mutates the index",
        ),
    ]


def attach_knowledge(
    harness: Harness,
    index: KnowledgeIndex | None = None,
    *,
    docs_dir: Path | None = None,
) -> KnowledgeIndex:
    """把知识工具挂到 Harness，并可选预加载 docs_dir。"""
    idx = index or KnowledgeIndex()
    if docs_dir is not None:
        idx.ingest_dir(Path(docs_dir))
    for tool in knowledge_tools(idx):
        harness.register_tool(tool)
    harness.permission.add_rules(knowledge_permission_rules())
    harness.context.add_fragment(
        f"Knowledge tools enabled ({len(idx.chunks)} chunks). "
        "Use knowledge_search before answering factual questions about loaded docs. "
        "Cite chunk ids like [1] when using retrieved text."
    )
    return idx
