"""编码 Agent 冒烟：假模型写文件。"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents.coding.factory import build_coding_agent
from tests.fakes import FakeModelClient, text_response, tool_call_response


@pytest.mark.asyncio
async def test_coding_agent_writes_file(tmp_path: Path) -> None:
    """装配后的编码 Agent 能走完 write_file 循环。"""
    fake = FakeModelClient(
        script=[
            tool_call_response(
                "write_file",
                {"path": "hello.py", "content": "print('hi')\n"},
            ),
            text_response("Added hello.py"),
        ]
    )
    harness = build_coding_agent(
        tmp_path,
        model=fake,
        auto_approve=True,
        verbose=False,
        sandbox="direct",
    )
    try:
        session = harness.session(workspace=tmp_path)
        result = await session.run("add hello.py")
        assert "hello.py" in result.final_text or result.tool_call_count >= 1
        assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hi')\n"
        assert "coding" in harness.packs
        assert harness.context.skill_catalog is not None
        assert "code-style" in harness.context.skill_catalog.skills
    finally:
        await harness.aclose()
