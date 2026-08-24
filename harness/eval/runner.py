"""场景评测运行器：用 Fake/真实模型跑一组用例并打分。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness import Harness
from harness.config import HarnessConfig
from harness.core.model import ModelClient
from harness.core.permission import AutoApprove
from harness.core.types import TurnResult

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """单条评测用例。"""

    id: str
    query: str
    # 期望至少调用过的工具名
    expect_tools: list[str] = field(default_factory=list)
    # 最终回复应包含的子串
    expect_text_contains: list[str] = field(default_factory=list)
    # 期望工作区内存在的相对路径
    expect_files: list[str] = field(default_factory=list)
    # 可选：自定义断言 (harness, result, workspace) -> None（抛 AssertionError 即失败）
    custom: Callable[[Harness, TurnResult, Path], None] | None = None


@dataclass
class CaseResult:
    """单条用例结果。"""

    case_id: str
    passed: bool
    reason: str = ""
    tool_calls: int = 0
    final_text: str = ""


@dataclass
class EvalReport:
    """评测汇总。"""

    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def summary(self) -> str:
        """人类可读摘要。"""
        lines = [f"Eval: {self.passed}/{self.total} passed"]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{mark}] {r.case_id}: {r.reason or r.final_text[:60]}")
        return "\n".join(lines)


class EvalRunner:
    """对一组 EvalCase 逐条新建 session 执行。"""

    def __init__(
        self,
        *,
        workspace: Path,
        model: ModelClient,
        pack_factory: Callable[[Path], list[Any]] | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.model = model
        self.pack_factory = pack_factory
        self.config_overrides = dict(config_overrides or {})

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        """顺序跑完全部用例。"""
        report = EvalReport()
        for case in cases:
            report.results.append(await self._run_one(case))
        return report

    async def _run_one(self, case: EvalCase) -> CaseResult:
        """跑单条用例并判定。"""
        data_dir = self.workspace / ".harness" / "eval" / case.id
        data_dir.mkdir(parents=True, exist_ok=True)
        # 默认关闭 cron；调用方可经 config_overrides 覆盖
        cfg_kwargs: dict[str, Any] = {
            "model_id": "test-model",
            "workspace": self.workspace,
            "data_dir": data_dir,
            "auto_approve": True,
            "enable_cron": False,
        }
        cfg_kwargs.update(
            {k: v for k, v in self.config_overrides.items() if k in HarnessConfig.model_fields}
        )
        cfg = HarnessConfig(**cfg_kwargs)
        harness = Harness.create(cfg, model=self.model, approval=AutoApprove(True))
        try:
            # 默认挂上工作区工具；业务 Pack 由 pack_factory / setup 注入
            harness.register_workspace_tools(
                self.workspace,
                sandbox_preference=str(cfg.sandbox),
            )
            if self.pack_factory:
                for pack in self.pack_factory(self.workspace):
                    harness.register_pack(pack)
            session = harness.session(workspace=self.workspace)
            result = await session.run(case.query)
            return self._judge(case, harness, result)
        except Exception as exc:
            logger.exception("eval case %s crashed", case.id)
            return CaseResult(case_id=case.id, passed=False, reason=f"crash: {exc}")
        finally:
            await harness.aclose()

    def _judge(self, case: EvalCase, harness: Harness, result: TurnResult) -> CaseResult:
        """根据期望字段判定通过与否。"""
        reasons: list[str] = []
        for needle in case.expect_text_contains:
            if needle.lower() not in result.final_text.lower():
                reasons.append(f"missing text {needle!r}")

        for rel in case.expect_files:
            if not (self.workspace / rel).exists():
                reasons.append(f"missing file {rel}")

        if case.expect_tools and result.tool_call_count < len(case.expect_tools):
            # 至少调用次数不少于期望工具数（弱检查）
            reasons.append(
                f"tool_call_count={result.tool_call_count} < expected {len(case.expect_tools)}"
            )

        if case.custom is not None:
            try:
                case.custom(harness, result, self.workspace)
            except AssertionError as exc:
                reasons.append(str(exc) or "custom assertion failed")
            except Exception as exc:
                reasons.append(f"custom error: {exc}")

        # 强工具名检查：扫描 transcript 文件
        if case.expect_tools:
            missing = self._missing_tools_from_transcript(harness, case.expect_tools)
            if missing:
                reasons.append(f"missing tools {missing}")

        passed = not reasons
        return CaseResult(
            case_id=case.id,
            passed=passed,
            reason="; ".join(reasons) if reasons else "ok",
            tool_calls=result.tool_call_count,
            final_text=result.final_text,
        )

    def _missing_tools_from_transcript(self, harness: Harness, expected: list[str]) -> list[str]:
        """同步扫描 JSONL transcript，检查是否出现过期望工具名。"""
        import json

        tdir = harness.config.resolved_transcript_dir()
        if not tdir.exists():
            return list(expected)
        found: set[str] = set()
        for path in tdir.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    content = payload.get("message", {}).get("content")
                except json.JSONDecodeError:
                    continue
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        found.add(str(block.get("name")))
        return [t for t in expected if t not in found]
