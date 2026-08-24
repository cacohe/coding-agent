"""记忆工具与 Stop 时的启发式抽取。"""

from __future__ import annotations

import logging
import re

from harness.core.hooks import HookAction, HookEvent, HookOutcome
from harness.core.registry import Tool
from harness.core.types import RiskLevel, ToolResult, ToolSpec
from harness.memory import FileMemoryStore

logger = logging.getLogger(__name__)


def memory_tools(store: FileMemoryStore) -> list[Tool]:
    """暴露 write_memory / read_memory / list_memories。"""

    async def write_memory(
        name: str,
        description: str,
        body: str,
        mem_type: str = "project",
    ) -> ToolResult:
        path = store.write(name, description=description, body=body, mem_type=mem_type)
        return ToolResult(
            content=f"Saved memory {name!r} to {path.name}",
            artifacts={"path": str(path)},
        )

    async def read_memory(name: str) -> ToolResult:
        store.reload()
        record = store.records.get(name)
        if record is None:
            known = ", ".join(sorted(store.records)) or "(none)"
            return ToolResult(
                content=f"Error: unknown memory {name!r}. Known: {known}",
                is_error=True,
            )
        return ToolResult(content=f"# {record.name}\n{record.description}\n\n{record.body}")

    async def list_memories() -> ToolResult:
        store.reload()
        if not store.records:
            return ToolResult(content="(no memories)")
        lines = [f"- {r.name} ({r.mem_type}): {r.description}" for r in store.records.values()]
        return ToolResult(content="\n".join(lines))

    return [
        Tool(
            spec=ToolSpec(
                name="write_memory",
                description="Persist a reusable memory across sessions.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "body": {"type": "string"},
                        "mem_type": {
                            "type": "string",
                            "enum": ["user", "feedback", "project", "reference"],
                            "default": "project",
                        },
                    },
                    "required": ["name", "description", "body"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=write_memory,
        ),
        Tool(
            spec=ToolSpec(
                name="read_memory",
                description="Load the full body of a named memory.",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                risk=RiskLevel.READ,
            ),
            handler=read_memory,
        ),
        Tool(
            spec=ToolSpec(
                name="list_memories",
                description="List memory index entries.",
                input_schema={"type": "object", "properties": {}},
                risk=RiskLevel.READ,
            ),
            handler=list_memories,
        ),
    ]


def make_memory_extract_hook(store: FileMemoryStore):
    """Stop 钩子：从本轮对话启发式抽取「用户偏好」类记忆。

    不调用 LLM，避免额外成本；命中明确句式才写入。
    """

    async def on_stop(ctx: dict) -> HookOutcome | None:
        session = ctx.get("session")
        if session is None:
            return None
        # 拼接本轮用户话与最终助手文本
        texts: list[str] = []
        if getattr(session, "latest_user_text", ""):
            texts.append(session.latest_user_text)
        response = ctx.get("response")
        if response is not None and getattr(response, "text", None):
            texts.append(response.text)
        blob = "\n".join(texts)
        extracted = _extract_preferences(blob)
        for name, description, body in extracted:
            try:
                store.write(name, description=description, body=body, mem_type="user")
                logger.info("auto-extracted memory %s", name)
            except Exception:
                logger.exception("failed to write extracted memory %s", name)
        return HookOutcome(action=HookAction.CONTINUE)

    return HookEvent.STOP, on_stop, "memory_extract", 90


_PREF_PATTERNS = [
    # 中文：请记住… / 我喜欢…
    re.compile(r"(?:请记住|记住|我喜欢|偏好)[:：\s]*(.+)"),
    # 英文：remember that… / I prefer…
    re.compile(r"(?i)(?:remember that|please remember|i prefer)\s+(.+)"),
]


def _extract_preferences(text: str) -> list[tuple[str, str, str]]:
    """从文本中抽出偏好三元组 (name, description, body)。"""
    found: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pattern in _PREF_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            body = match.group(1).strip().rstrip("。.")
            if len(body) < 3:
                continue
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", body[:40]).strip("-").lower() or "pref"
            found.append((f"pref-{slug}", body[:80], body))
            break
    return found
