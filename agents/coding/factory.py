"""装配编码产品用的 Harness（工作区工具、CodingPack、skills）。"""

from __future__ import annotations

from pathlib import Path

from harness import AutoApprove, Harness, HarnessConfig, StdinApproval
from harness.core.model import ModelClient
from harness.core.permission import ApprovalPort
from harness.skills import SkillCatalog

from agents.coding.pack import CodingPack
from agents.coding.prompts import CODING_SYSTEM_PROMPT


def create_coding_harness(
    workspace: Path | None = None,
    *,
    model_id: str = "openai/gpt-4o",
    model: ModelClient | None = None,
    auto_approve: bool = False,
    approval: ApprovalPort | None = None,
    verbose: bool = True,
    enable_memory: bool = True,
    enable_tasks: bool = True,
    sandbox: str = "auto",
    api_base: str | None = None,
    api_key: str | None = None,
    max_iterations: int = 60,
) -> Harness:
    """创建已挂载工作区工具、CodingPack 与 skills 的编码 Harness。

    未传 model 时经 LiteLLMModelClient 连接 model_id；测试可注入自定义 ModelClient。
    """
    ws = (workspace or Path.cwd()).resolve()
    data_dir = ws / ".harness" / "coding-agent"

    cfg = HarnessConfig(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
        workspace=ws,
        data_dir=data_dir,
        system_prompt=CODING_SYSTEM_PROMPT,
        auto_approve=auto_approve,
        enable_memory=enable_memory,
        enable_tasks=enable_tasks,
        enable_cron=False,
        enable_teams=False,
        sandbox=sandbox,
        max_iterations=max_iterations,
    )

    if approval is None:
        approval = AutoApprove(True) if auto_approve else StdinApproval()

    harness = Harness.create(cfg, model=model, approval=approval)
    harness.register_workspace_tools(ws, sandbox_preference=sandbox)

    pack = CodingPack(ws, verbose=verbose)
    harness.register_pack(pack)

    catalog = SkillCatalog(pack.skills_dirs())
    catalog.scan()
    harness.context.skill_catalog = catalog
    harness.register_tool(catalog.as_load_tool())

    return harness
