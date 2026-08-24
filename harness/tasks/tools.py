"""把 TaskStore 暴露为模型可调用的工具。"""

from __future__ import annotations

import json
from typing import Any

from harness.core.registry import Tool
from harness.core.types import RiskLevel, ToolResult, ToolSpec


def task_tools(store: Any) -> list[Tool]:
    """生成 create/list/claim/complete 四个任务工具。"""

    async def create_task(
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
    ) -> ToolResult:
        task = store.create(subject, description=description, blocked_by=blocked_by)
        return ToolResult(
            content=json.dumps(
                {
                    "id": task.id,
                    "subject": task.subject,
                    "status": task.status,
                    "blocked_by": task.blocked_by,
                },
                ensure_ascii=False,
            ),
            artifacts={"task_id": task.id},
        )

    async def list_tasks(status: str | None = None) -> ToolResult:
        tasks = store.list_tasks(status=status)
        payload = [
            {
                "id": t.id,
                "subject": t.subject,
                "status": t.status,
                "owner": t.owner,
                "blocked_by": t.blocked_by,
            }
            for t in tasks
        ]
        return ToolResult(content=json.dumps(payload, ensure_ascii=False) or "[]")

    async def claim_task(task_id: str, owner: str = "agent") -> ToolResult:
        task = store.claim(task_id, owner)
        if task is None:
            return ToolResult(
                content=f"Error: cannot claim {task_id} (missing, not pending, or blocked)",
                is_error=True,
            )
        return ToolResult(
            content=json.dumps(
                {"id": task.id, "status": task.status, "owner": task.owner},
                ensure_ascii=False,
            )
        )

    async def complete_task(task_id: str) -> ToolResult:
        task = store.complete(task_id)
        if task is None:
            return ToolResult(content=f"Error: unknown task {task_id}", is_error=True)
        return ToolResult(
            content=json.dumps({"id": task.id, "status": task.status}, ensure_ascii=False)
        )

    return [
        Tool(
            spec=ToolSpec(
                name="create_task",
                description="Create a durable task in the task graph.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "description": {"type": "string"},
                        "blocked_by": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "task ids that must complete first",
                        },
                    },
                    "required": ["subject"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=create_task,
        ),
        Tool(
            spec=ToolSpec(
                name="list_tasks",
                description="List tasks, optionally filtered by status.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                        }
                    },
                },
                risk=RiskLevel.READ,
            ),
            handler=list_tasks,
        ),
        Tool(
            spec=ToolSpec(
                name="claim_task",
                description="Atomically claim a pending ready task.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "owner": {"type": "string", "default": "agent"},
                    },
                    "required": ["task_id"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=claim_task,
        ),
        Tool(
            spec=ToolSpec(
                name="complete_task",
                description="Mark a task as completed.",
                input_schema={
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
                risk=RiskLevel.WRITE,
            ),
            handler=complete_task,
        ),
    ]
