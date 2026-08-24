"""技能目录：扫描 skills/*/SKILL.md，渐进披露。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from harness.core.registry import Tool
from harness.core.types import RiskLevel, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


@dataclass
class SkillRecord:
    """单条技能的内存表示。"""

    name: str
    description: str
    content: str  # 完整 SKILL.md
    path: Path


class SkillCatalog:
    """技能目录实现 ContextAssembler 所需的 catalog_text()。"""

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = [Path(r) for r in (roots or [])]
        self.skills: dict[str, SkillRecord] = {}

    def add_root(self, root: Path) -> None:
        """追加扫描根目录并立即重扫。"""
        self.roots.append(Path(root))
        self.scan()

    def scan(self) -> None:
        """扫描所有根下的 */SKILL.md。"""
        self.skills.clear()
        for root in self.roots:
            if not root.exists():
                logger.warning("skills root missing: %s", root)
                continue
            for manifest in sorted(root.glob("*/SKILL.md")):
                self._load_manifest(manifest, root)

    def _load_manifest(self, manifest: Path, root: Path) -> None:
        """解析单个 SKILL.md（YAML frontmatter + body）。"""
        try:
            # 防止符号链接逃逸
            if not manifest.resolve().is_relative_to(root.resolve()):
                logger.warning("skill escapes root: %s", manifest)
                return
            raw = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("cannot read skill %s: %s", manifest, exc)
            return

        metadata, body = _parse_frontmatter(raw)
        name = str(metadata.get("name") or manifest.parent.name).strip()
        description = str(metadata.get("description") or "").strip()
        if not description:
            # 回退：正文第一行
            description = body.split("\n", 1)[0].lstrip("# ").strip()
        self.skills[name] = SkillRecord(
            name=name,
            description=description,
            content=raw,
            path=manifest,
        )

    def catalog_text(self) -> str:
        """短目录：仅 name + description，供 system prompt。"""
        if not self.skills:
            return ""
        lines = [f"- {s.name}: {s.description}" for s in self.skills.values()]
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """返回完整技能正文；不存在则返回错误说明。"""
        skill = self.skills.get(name)
        if skill is None:
            known = ", ".join(sorted(self.skills)) or "(none)"
            return f"Error: unknown skill {name!r}. Known: {known}"
        return skill.content

    def as_load_tool(self) -> Tool:
        """生成 load_skill 工具，供注册到 ToolRegistry。"""

        async def load_skill(name: str) -> ToolResult:
            return ToolResult(content=self.load(name))

        return Tool(
            spec=ToolSpec(
                name="load_skill",
                description="Load the full SKILL.md for a named skill from the catalog.",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                risk=RiskLevel.READ,
            ),
            handler=load_skill,
        )


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """解析 YAML frontmatter；失败则返回空元数据 + 原文。"""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    try:
        meta = yaml.safe_load(match.group(1)) or {}
        if not isinstance(meta, dict):
            meta = {}
    except yaml.YAMLError:
        meta = {}
    return meta, match.group(2)
