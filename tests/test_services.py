"""记忆、任务通知、Cron、Goal 门控、MCP、知识检索。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from harness import Harness
from harness.config import HarnessConfig
from harness.core.permission import AutoApprove, PermissionDecision, PermissionRule
from harness.core.types import RiskLevel, StopAction
from harness.knowledge import KnowledgeIndex, attach_knowledge
from harness.mcp import MCPGateway
from tests.fakes import FakeModelClient, text_response, tool_call_response


@pytest.mark.asyncio
async def test_memory_select_and_extract(tmp_path: Path) -> None:
    """记忆写入后应进入 system；Stop 时可从「请记住」抽取。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=True,
        enable_tasks=False,
        enable_cron=False,
    )
    fake = FakeModelClient(script=[text_response("收到，已记住偏好")])
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    assert harness.memory is not None
    harness.memory.write(
        "pref-tabs",
        description="User prefers tabs",
        body="User prefers tabs for indentation",
        mem_type="user",
    )
    session = harness.session(workspace=tmp_path)
    await session.run("请记住 我喜欢使用 tabs 缩进")
    # 抽取钩子应写入新记忆
    harness.memory.reload()
    assert any(
        "tabs" in name.lower() or "tabs" in r.body.lower()
        for name, r in harness.memory.records.items()
    )
    # 下一轮 system 应能召回
    fake2 = FakeModelClient(script=[text_response("ok")])
    harness.loop.model = fake2
    await session.run("how should I indent code with tabs?")
    system = fake2.calls[0].system.lower()
    assert "tabs" in system or "memory" in system


@pytest.mark.asyncio
async def test_background_notification_injected(tmp_path: Path) -> None:
    """后台任务完成后，下一轮 LLM 前应注入通知。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=True,
        enable_cron=False,
    )
    fake = FakeModelClient(script=[text_response("saw notification")])
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    assert harness.background is not None
    harness.background.spawn("slow", lambda: "job-done-42")
    # 等待线程完成
    await asyncio.sleep(0.1)
    session = harness.session(workspace=tmp_path)
    result = await session.run("check jobs")
    assert result.final_text == "saw notification"
    # 模型看到的 messages 应含通知
    joined = "\n".join(m.text() for m in fake.calls[0].messages)
    assert "job-done-42" in joined
    assert "harness_notifications" in joined


@pytest.mark.asyncio
async def test_cron_tick_injects(tmp_path: Path) -> None:
    """CronScheduler.tick 产生的通知会被注入。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=False,
        enable_cron=True,
    )
    fake = FakeModelClient(script=[text_response("cron ok")])
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    assert harness.cron is not None
    harness.cron.schedule("heartbeat check", interval_seconds=60, fire_immediately=True)
    harness.cron.tick()
    session = harness.session(workspace=tmp_path)
    await session.run("hi")
    joined = "\n".join(m.text() for m in fake.calls[0].messages)
    assert "heartbeat check" in joined
    await harness.aclose()


@pytest.mark.asyncio
async def test_goal_stop_policy_continues_then_allows(tmp_path: Path) -> None:
    """目标未满足时 CONTINUE，满足后 ALLOW。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=False,
        enable_cron=False,
        enable_goal_stop=True,
        goal_max_continues=3,
    )
    # 第一次回复不含目标词 → continue；第二次含 report 与 done → allow
    fake = FakeModelClient(
        script=[
            text_response("still working"),
            text_response("Created the quarterly report file and done"),
        ]
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    session = harness.session(workspace=tmp_path, goal="create quarterly report")
    result = await session.run("start")
    assert result.stop.action == StopAction.ALLOW
    assert len(fake.calls) == 2
    assert "report" in result.final_text.lower()


@pytest.mark.asyncio
async def test_task_tools_via_agent(tmp_path: Path) -> None:
    """模型可通过 create_task 工具创建任务。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=True,
        enable_cron=False,
    )
    fake = FakeModelClient(
        script=[
            tool_call_response("create_task", {"subject": "ship milestone"}),
            text_response("task created"),
        ]
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    session = harness.session(workspace=tmp_path)
    result = await session.run("create a task")
    assert result.tool_call_count == 1
    assert harness.tasks is not None
    tasks = harness.tasks.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].subject == "ship milestone"


@pytest.mark.asyncio
async def test_mcp_local_tool_in_pool(tmp_path: Path) -> None:
    """本地 MCP 工具以 mcp__ 前缀进入同一 ToolPool。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=False,
        enable_cron=False,
    )
    # 网络工具默认 DENY；测试中允许该 mcp 工具
    fake = FakeModelClient(
        script=[
            tool_call_response("mcp__demo__echo", {"text": "hi"}),
            text_response("echoed"),
        ]
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    gateway = MCPGateway()

    async def echo(text: str = "") -> str:
        return f"echo:{text}"

    gateway.register_local_tool(
        "demo",
        "echo",
        echo,
        description="Echo text",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        risk=RiskLevel.READ,
    )
    await harness.attach_mcp(gateway)
    harness.permission.add_rules(
        [
            PermissionRule(
                tool_pattern="mcp__demo__echo",
                decision=PermissionDecision.ALLOW,
                reason="test allow",
            )
        ]
    )
    session = harness.session(workspace=tmp_path)
    result = await session.run("echo")
    assert result.final_text == "echoed"
    # tool_result 应含 echo:hi
    tool_msg = fake.calls[1].messages[-1].text()
    assert "echo:hi" in tool_msg
    await harness.aclose()


@pytest.mark.asyncio
async def test_knowledge_pack_search(tmp_path: Path) -> None:
    """knowledge_search 能命中已摄入文档。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=False,
        enable_cron=False,
    )
    fake = FakeModelClient(
        script=[
            tool_call_response("knowledge_search", {"query": "cacohe mascot"}),
            text_response("Cacohe is the project mascot"),
        ]
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    index = KnowledgeIndex()
    index.add_document("wiki", "Cacohe is the project mascot living in codespace.")
    attach_knowledge(harness, index)
    session = harness.session(workspace=tmp_path)
    result = await session.run("who is cacohe?")
    assert "mascot" in result.final_text.lower()
    retrieved = fake.calls[1].messages[-1].text().lower()
    assert "cacohe" in retrieved
