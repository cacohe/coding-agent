"""文件系统工具实现。"""

from __future__ import annotations

import glob as globmod

from harness.core.types import ToolResult
from harness.tools.workspace import WorkspaceGuard


def read_file(guard: WorkspaceGuard, path: str, limit: int | None = None) -> ToolResult:
    """读取文本文件；可选限制行数。"""
    try:
        file_path = guard.resolve(path)
        if not file_path.exists():
            return ToolResult(content=f"Error: file not found: {path}", is_error=True)
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return ToolResult(content="\n".join(lines) if lines else "(empty)")
    except Exception as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)


def write_file(guard: WorkspaceGuard, path: str, content: str) -> ToolResult:
    """写入文本文件，自动创建父目录。"""
    try:
        file_path = guard.resolve(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return ToolResult(
            content=f"Wrote {len(content)} bytes to {path}",
            artifacts={"path": str(file_path)},
        )
    except Exception as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)


def edit_file(guard: WorkspaceGuard, path: str, old_text: str, new_text: str) -> ToolResult:
    """替换文件中第一处 old_text。"""
    try:
        file_path = guard.resolve(path)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if old_text not in text:
            return ToolResult(content=f"Error: old_text not found in {path}", is_error=True)
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return ToolResult(content=f"Edited {path}")
    except Exception as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)


def glob_files(guard: WorkspaceGuard, pattern: str) -> ToolResult:
    """在 workspace 内做 glob，并再次校验每个命中路径。"""
    try:
        matches: list[str] = []
        for match in globmod.glob(pattern, root_dir=guard.root, recursive=True):
            resolved = (guard.root / match).resolve()
            if resolved.is_relative_to(guard.root):
                matches.append(match)
        return ToolResult(content="\n".join(matches) if matches else "(no matches)")
    except Exception as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)
