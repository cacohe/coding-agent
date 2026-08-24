"""AgentLoop 契约测试：用 FakeModelClient 验证工具循环与权限。"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import Harness
from harness.config import HarnessConfig
from harness.core.permission import AutoApprove, PermissionDecision, PermissionRule
from harness.core.types import RiskLevel, StopAction
from tests.fakes import FakeModelClient, text_response, tool_call_response


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """临时工作区。"""
    return tmp_path


@pytest.mark.asyncio
async def test_loop_tool_then_stop(workspace: Path) -> None:
    """模型先调 write_file，再返回文本；文件应落盘。"""
    fake = FakeModelClient(
        script=[
            tool_call_response(
                "write_file",
                {"path": "out.txt", "content": "ping"},
            ),
            text_response("done"),
        ]
    )
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=workspace,
        data_dir=workspace / ".harness",
        auto_approve=True,
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    harness.register_workspace_tools(workspace)
    session = harness.session(workspace=workspace)

    result = await session.run("write a file")
    assert result.final_text == "done"
    assert result.tool_call_count == 1
    assert result.stop.action == StopAction.ALLOW
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "ping"
    # Fake 模型应被调用两次（tool 轮 + 最终轮）
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_permission_denies_write_without_approval(workspace: Path) -> None:
    """未批准时写工具应返回 permission denied，且不写盘。"""
    fake = FakeModelClient(
        script=[
            tool_call_response(
                "write_file",
                {"path": "secret.txt", "content": "nope"},
            ),
            text_response("could not write"),
        ]
    )
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=workspace,
        data_dir=workspace / ".harness",
    )
    # 自动拒绝所有 ASK
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(False))
    harness.register_workspace_tools(workspace)
    # 强制 write 必须 ASK（pack 默认已是 ASK；再确保 default 不是 ALLOW）
    harness.permission.add_rules(
        [
            PermissionRule(
                tool_pattern="write_file",
                risk=RiskLevel.WRITE,
                decision=PermissionDecision.ASK,
                reason="test ask",
            )
        ]
    )
    session = harness.session(workspace=workspace)
    result = await session.run("write")
    assert not (workspace / "secret.txt").exists()
    assert result.final_text == "could not write"
    # 第二轮请求的 messages 应包含 denied tool_result
    second = fake.calls[1]
    blob = str(second.messages[-1].content)
    assert "permission denied" in blob.lower() or "denied" in blob.lower()


@pytest.mark.asyncio
async def test_path_jail_blocks_escape(workspace: Path) -> None:
    """read_file 越出 workspace 应失败。"""
    fake = FakeModelClient(
        script=[
            tool_call_response("read_file", {"path": "../outside.txt"}),
            text_response("blocked"),
        ]
    )
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=workspace,
        data_dir=workspace / ".harness",
        auto_approve=True,
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    harness.register_workspace_tools(workspace)
    session = harness.session(workspace=workspace)
    await session.run("read outside")
    second = fake.calls[1]
    text = second.messages[-1].text().lower()
    assert "escape" in text or "error" in text
