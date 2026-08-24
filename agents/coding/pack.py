"""编码场景 CapabilityPack：提示词片段、权限微调、工具调用可见性。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.core.hooks import HookAction, HookEvent, HookOutcome
from harness.core.pack import CapabilityPack
from harness.core.permission import PermissionDecision, PermissionRule
from harness.core.session import SessionState
from harness.core.types import RiskLevel

from agents.coding.prompts import CODING_FRAGMENT


class CodingPack(CapabilityPack):
    """面向本地编码任务的能力包（不 fork AgentLoop）。"""

    def __init__(self, workspace: Path, *, verbose: bool = True) -> None:
        self.workspace = Path(workspace).resolve()
        self.verbose = verbose

    @property
    def name(self) -> str:
        return "coding"

    def policies(self) -> list[PermissionRule]:
        """读类与技能默认放行；写/执行仍走全局 ASK（可用 --yes 自动批）。"""
        return [
            PermissionRule(
                tool_pattern="load_skill",
                decision=PermissionDecision.ALLOW,
                reason="loading skills is read-only",
            ),
            PermissionRule(
                tool_pattern="glob",
                risk=RiskLevel.READ,
                decision=PermissionDecision.ALLOW,
                reason="workspace listing is read-only",
            ),
            PermissionRule(
                tool_pattern="read_file",
                risk=RiskLevel.READ,
                decision=PermissionDecision.ALLOW,
                reason="file reads are allowed",
            ),
        ]

    def hooks(self) -> list[tuple[HookEvent, Any, str, int]]:
        if not self.verbose:
            return []

        async def log_pre_tool(ctx: dict[str, Any]) -> HookOutcome | None:
            tool = ctx.get("tool")
            if tool is None:
                return None
            args = getattr(tool, "input", {}) or {}
            preview = json.dumps(args, ensure_ascii=False)
            if len(preview) > 160:
                preview = preview[:157] + "..."
            print(f"  → {tool.name}({preview})")
            return HookOutcome(action=HookAction.CONTINUE)

        async def log_post_tool(ctx: dict[str, Any]) -> HookOutcome | None:
            tool = ctx.get("tool")
            result = ctx.get("result")
            if tool is None or result is None:
                return None
            err = " ERROR" if getattr(result, "is_error", False) else ""
            content = str(getattr(result, "content", "") or "")
            snippet = content.replace("\n", " ")
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            print(f"  ← {tool.name}{err}: {snippet}")
            return HookOutcome(action=HookAction.CONTINUE)

        return [
            (HookEvent.PRE_TOOL_USE, log_pre_tool, "coding.verbose_pre", 10),
            (HookEvent.POST_TOOL_USE, log_post_tool, "coding.verbose_post", 10),
        ]

    def system_fragments(self, session: SessionState | None = None) -> list[str]:
        return [
            CODING_FRAGMENT,
            f"Workspace root: {self.workspace}",
        ]

    def skills_dirs(self) -> list[Path]:
        """仓库 skills/ + agents/coding/skills（若存在）。"""
        repo_root = Path(__file__).resolve().parents[2]
        dirs = [repo_root / "skills"]
        local = Path(__file__).resolve().parent / "skills"
        if local.exists():
            dirs.append(local)
        return dirs
