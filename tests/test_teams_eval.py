"""进程队友、沙箱、transcript 回放与评测。"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import Harness
from harness.config import HarnessConfig
from harness.core.permission import AutoApprove
from harness.core.transcript import JsonlTranscriptStore
from harness.eval import EvalCase, EvalRunner, TranscriptReplayer
from harness.teams import Mailbox, TeamRuntime
from harness.tools.sandbox import DirectSandbox, detect_sandbox
from tests.fakes import FakeModelClient, text_response, tool_call_response


def test_detect_sandbox_direct_or_bwrap() -> None:
    """auto 模式至少返回一个可用后端。"""
    backend = detect_sandbox("auto")
    assert backend.name in {"direct", "bubblewrap"}


@pytest.mark.asyncio
async def test_direct_sandbox_runs_python(tmp_path: Path) -> None:
    """DirectSandbox 能执行简单 python -c。"""
    sandbox = DirectSandbox()
    result = await sandbox.run(
        ["python", "-c", "print('sandbox-ok')"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert result.exit_code == 0
    assert "sandbox-ok" in result.stdout
    assert result.backend == "direct"


def test_team_mailbox_and_process_worker(tmp_path: Path) -> None:
    """进程队友接收 task 并回传 result。"""
    mailbox = Mailbox(tmp_path / "mail.db")
    team = TeamRuntime(mailbox, lead_name="lead")
    try:
        team.spawn("worker-a")
        # ready
        ready = team.wait_result(timeout=5.0)
        assert any(r.get("status") == "ready" for r in ready)
        team.assign("worker-a", "summarize module X")
        done = team.wait_result(timeout=5.0)
        assert any(r.get("status") == "done" and "module X" in str(r.get("echo", "")) for r in done)
    finally:
        team.shutdown_all()


@pytest.mark.asyncio
async def test_team_tools_via_harness(tmp_path: Path) -> None:
    """通过 spawn_teammate / assign / wait 工具完成协作。"""
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=False,
        enable_cron=False,
        enable_teams=True,
        sandbox="direct",
    )
    fake = FakeModelClient(
        script=[
            tool_call_response("spawn_teammate", {"name": "scout"}),
            tool_call_response("assign_teammate", {"name": "scout", "text": "find TODOs"}),
            tool_call_response("wait_teammate", {"timeout_seconds": 5}),
            text_response("teammate finished"),
        ]
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    assert harness.team is not None
    session = harness.session(workspace=tmp_path)
    try:
        result = await session.run("use a teammate")
        assert result.final_text == "teammate finished"
        assert result.tool_call_count == 3
        # wait 的 tool_result 应含 done/echo
        wait_msg = fake.calls[3].messages[-1].text()
        assert "find TODOs" in wait_msg or "done" in wait_msg.lower()
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_transcript_replayer_metrics(tmp_path: Path) -> None:
    """回放器能统计 tool_use / tool 错误。"""
    store = JsonlTranscriptStore(tmp_path / "transcripts")
    cfg = HarnessConfig(
        model_id="test-model",
        workspace=tmp_path,
        data_dir=tmp_path / ".harness",
        transcript_dir=tmp_path / "transcripts",
        auto_approve=True,
        enable_memory=False,
        enable_tasks=False,
        enable_cron=False,
        sandbox="direct",
    )
    fake = FakeModelClient(
        script=[
            tool_call_response("write_file", {"path": "a.txt", "content": "x"}),
            text_response("wrote a.txt"),
        ]
    )
    harness = Harness.create(cfg, model=fake, approval=AutoApprove(True))
    harness.register_workspace_tools(tmp_path, sandbox_preference="direct")
    session = harness.session(workspace=tmp_path)
    result = await session.run("write")
    await harness.aclose()

    replayer = TranscriptReplayer(store)
    metrics = await replayer.summarize(result.session_id)
    assert metrics.tool_calls >= 1
    assert "write_file" in metrics.tools_used
    assert metrics.assistant_turns >= 1


@pytest.mark.asyncio
async def test_eval_runner_pass(tmp_path: Path) -> None:
    """EvalRunner 对脚本化用例给出 PASS。"""
    # 每个 case 会 new Harness；用 responder 按调用次数返回
    calls = {"n": 0}

    def responder(request):
        from tests.fakes import text_response, tool_call_response

        calls["n"] += 1
        # 第一次：写文件；第二次：结束
        if calls["n"] % 2 == 1:
            return tool_call_response(
                "write_file",
                {"path": "eval_ok.txt", "content": "ok"},
            )
        return text_response("created eval_ok.txt")

    fake = FakeModelClient(responder=responder)
    runner = EvalRunner(
        workspace=tmp_path,
        model=fake,
        pack_factory=None,
        config_overrides={
            "enable_memory": False,
            "enable_tasks": False,
            "enable_cron": False,
            "sandbox": "direct",
        },
    )
    report = await runner.run(
        [
            EvalCase(
                id="write-ok",
                query="write eval file",
                expect_tools=["write_file"],
                expect_text_contains=["eval_ok"],
                expect_files=["eval_ok.txt"],
            )
        ]
    )
    assert report.passed == 1
    assert report.failed == 0
