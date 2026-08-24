"""能力包（Capability Pack）协议。

场景差异（coding / knowledge / custom）通过 Pack 注入工具、规则、钩子与短系统片段，
不得 fork AgentLoop。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from harness.core.hooks import HookEvent, HookHandler
from harness.core.permission import PermissionRule
from harness.core.registry import Tool

if TYPE_CHECKING:
    from harness.core.session import SessionState


class CapabilityPack(ABC):
    """可插拔能力包接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """能力包唯一名。"""

    def tools(self) -> list[Tool]:
        """返回本包提供的工具；默认无工具。"""
        return []

    def skills_dirs(self) -> list[Path]:
        """额外技能目录。"""
        return []

    def policies(self) -> list[PermissionRule]:
        """权限规则（会插到门控规则表前部）。"""
        return []

    def hooks(self) -> list[tuple[HookEvent, HookHandler, str, int]]:
        """钩子：(event, handler, name, priority)。"""
        return []

    def system_fragments(self, session: SessionState | None = None) -> list[str]:
        """短系统提示片段；禁止塞入长文档（长文走 skills）。"""
        return []
