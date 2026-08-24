"""工作区路径 jail：所有文件工具必须经此解析。"""

from __future__ import annotations

from pathlib import Path


class WorkspaceGuard:
    """保证路径解析后仍位于 workspace 根之内。"""

    def __init__(self, root: Path) -> None:
        # resolve 去掉 .. 与符号链接歧义
        self.root = Path(root).resolve()

    def resolve(self, relative: str) -> Path:
        """把相对（或绝对）路径规范到 workspace 内；越界抛 ValueError。"""
        path = (
            (self.root / relative).resolve()
            if not Path(relative).is_absolute()
            else Path(relative).resolve()
        )
        # Python 3.12: is_relative_to
        if not path.is_relative_to(self.root):
            raise ValueError(f"Path escapes workspace: {relative}")
        return path
