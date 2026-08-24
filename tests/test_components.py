"""工具、技能、任务、压缩等单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness.core.context import BudgetCompactor
from harness.core.hooks import HookAction, HookBus, HookEvent, HookOutcome
from harness.core.registry import ToolRegistry
from harness.core.types import Message, TextBlock, ToolResultBlock
from harness.skills import SkillCatalog
from harness.tasks import TaskStore
from harness.tools import register_builtin_tools


@pytest.mark.asyncio
async def test_workspace_read_write(tmp_path: Path) -> None:
    """内置 read/write 在 jail 内往返。"""
    registry = ToolRegistry()
    register_builtin_tools(registry, tmp_path)
    pool = registry.assemble()
    write = pool.get("write_file")
    read = pool.get("read_file")
    assert write is not None and read is not None
    await write.execute({"path": "a.txt", "content": "hello"})
    result = await read.execute({"path": "a.txt"})
    assert result.content == "hello"


@pytest.mark.asyncio
async def test_hook_deny_short_circuits() -> None:
    """PreToolUse deny 应被总线短路返回。"""
    bus = HookBus()

    async def deny(_ctx):
        return HookOutcome(action=HookAction.DENY, reason="nope")

    async def never(_ctx):
        raise AssertionError("should not run after deny")

    bus.register(HookEvent.PRE_TOOL_USE, deny, name="deny", priority=10)
    bus.register(HookEvent.PRE_TOOL_USE, never, name="never", priority=20)
    outcome = await bus.emit(HookEvent.PRE_TOOL_USE, {})
    assert outcome.denied
    assert outcome.reason == "nope"


def test_skill_catalog_scan(tmp_path: Path) -> None:
    """扫描 SKILL.md 并生成目录文本。"""
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo skill\n---\n\n# Body\n",
        encoding="utf-8",
    )
    catalog = SkillCatalog([tmp_path])
    catalog.scan()
    assert "demo" in catalog.skills
    assert "A demo skill" in catalog.catalog_text()
    assert "# Body" in catalog.load("demo")


def test_task_store_claim_and_complete(tmp_path: Path) -> None:
    """任务创建、认领、完成。"""
    store = TaskStore(tmp_path / "tasks.db")
    t1 = store.create("first")
    t2 = store.create("second", blocked_by=[t1.id])
    # 依赖未完成时不能认领 t2
    assert store.claim(t2.id, "agent-a") is None
    claimed = store.claim(t1.id, "agent-a")
    assert claimed is not None and claimed.status == "in_progress"
    store.complete(t1.id)
    claimed2 = store.claim(t2.id, "agent-b")
    assert claimed2 is not None
    assert claimed2.owner == "agent-b"


@pytest.mark.asyncio
async def test_budget_compactor_truncates_tool_result() -> None:
    """过长 tool_result 应被截断。"""
    long = "x" * 20_000
    messages = [
        Message(
            role="user",
            content=[ToolResultBlock(tool_use_id="1", content=long)],
        ),
        Message(role="assistant", content=[TextBlock(text="ok")]),
    ]
    compactor = BudgetCompactor(max_tool_result_chars=100)
    out = await compactor.prepare(messages)
    block = out[0].content[0]
    assert isinstance(block, ToolResultBlock)
    assert len(block.content) < 200
    assert "truncated" in block.content
