"""门面 API：Harness.create / session / register_*。

优先从 `agent_harness` 导入；高级定制可深入 `agent_harness.core`。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from harness.config import HarnessConfig
from harness.core.context import BudgetCompactor, ContextAssembler
from harness.core.hooks import HookBus, HookEvent, HookHandler
from harness.core.loop import AgentLoop
from harness.core.model import ModelClient
from harness.core.notifications import NotificationHub
from harness.core.pack import CapabilityPack
from harness.core.permission import (
    ApprovalPort,
    AutoApprove,
    PermissionDecision,
    PermissionGate,
    PermissionRule,
    StdinApproval,
    default_coding_rules,
)
from harness.core.registry import Tool, ToolRegistry
from harness.core.session import Session, SessionState, new_session_id
from harness.core.stop_policy import (
    AllowStopPolicy,
    GoalStopPolicy,
    MaxIterationStopPolicy,
    StopPolicy,
)
from harness.core.transcript import JsonlTranscriptStore

logger = logging.getLogger(__name__)


class Harness:
    """Agent Harness 运行时。"""

    def __init__(
        self,
        config: HarnessConfig,
        *,
        model: ModelClient,
        tools: ToolRegistry,
        hooks: HookBus,
        permission: PermissionGate,
        context: ContextAssembler,
        loop: AgentLoop,
        notifications: NotificationHub | None = None,
        memory: Any | None = None,
        tasks: Any | None = None,
        cron: Any | None = None,
        background: Any | None = None,
        mcp: Any | None = None,
        team: Any | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.tools = tools
        self.hooks = hooks
        self.permission = permission
        self.context = context
        self.loop = loop
        self.notifications = notifications
        self.memory = memory
        self.tasks = tasks
        self.cron = cron
        self.background = background
        self.mcp = mcp
        self.team = team
        self._packs: dict[str, CapabilityPack] = {}
        # 确保数据目录存在
        config.data_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(
        cls,
        config: HarnessConfig | None = None,
        *,
        model: ModelClient | None = None,
        approval: ApprovalPort | None = None,
        stop_policy: StopPolicy | None = None,
    ) -> Harness:
        """创建并装配完整 Harness 运行时。

        按 HarnessConfig 开关启用 memory / tasks / cron / teams 等能力。
        未传 model 时使用 LiteLLMModelClient（agent_harness.models）。
        """
        cfg = config or HarnessConfig()
        tools = ToolRegistry()
        hooks = HookBus()

        if approval is None:
            approval = AutoApprove(True) if cfg.auto_approve else StdinApproval()

        permission = PermissionGate(
            rules=default_coding_rules(),
            approval=approval,
        )
        context = ContextAssembler(base_system=cfg.system_prompt)
        compactor = BudgetCompactor(max_tokens=cfg.compact_max_tokens)
        transcript = JsonlTranscriptStore(cfg.resolved_transcript_dir())

        # Stop 策略链：Goal（可选）→ MaxIteration → Allow
        inner: StopPolicy = stop_policy or AllowStopPolicy()
        if cfg.enable_goal_stop and stop_policy is None:
            inner = GoalStopPolicy(AllowStopPolicy(), max_continues=cfg.goal_max_continues)
        stop = MaxIterationStopPolicy(inner, max_iterations=cfg.max_iterations)

        if model is None:
            from harness.models.litellm import LiteLLMModelClient

            model = LiteLLMModelClient(
                model_id=cfg.model_id,
                max_tokens=cfg.max_tokens,
                api_base=cfg.api_base,
                api_key=cfg.api_key,
            )

        memory = None
        tasks = None
        cron = None
        background = None
        team = None
        notifications = NotificationHub(transcript=transcript)

        # 沙箱默认后端（exec 工具使用）
        try:
            from harness.tools.exec import set_default_sandbox
            from harness.tools.sandbox import detect_sandbox

            set_default_sandbox(detect_sandbox(cfg.sandbox))
        except Exception:
            logger.exception("failed to configure sandbox backend")

        memory, tasks, cron, background = _wire_services(
            cfg, tools, hooks, permission, context, notifications
        )
        if cfg.enable_teams:
            team = _wire_teams(cfg, tools, permission)

        loop = AgentLoop(
            model=model,
            tools=tools,
            hooks=hooks,
            permission=permission,
            context=context,
            compactor=compactor,
            stop_policy=stop,
            transcript=transcript,
            notification_injector=notifications.inject,
        )
        return cls(
            cfg,
            model=model,
            tools=tools,
            hooks=hooks,
            permission=permission,
            context=context,
            loop=loop,
            notifications=notifications,
            memory=memory,
            tasks=tasks,
            cron=cron,
            background=background,
            team=team,
        )

    def register_tool(self, tool: Tool) -> None:
        """注册单个工具，并同步风险到权限门。"""
        self.tools.register(tool)
        self.permission.set_tool_risk(tool.name, tool.risk)

    def register_hook(
        self,
        event: HookEvent,
        handler: HookHandler,
        *,
        name: str,
        priority: int = 100,
    ) -> None:
        """注册生命周期钩子。"""
        self.hooks.register(event, handler, name=name, priority=priority)

    def register_pack(self, pack: CapabilityPack) -> None:
        """装载 CapabilityPack（工具、规则、钩子、系统片段）。"""
        logger.info("registering pack %s", pack.name)
        self._packs[pack.name] = pack
        for tool in pack.tools():
            self.register_tool(tool)
        self.permission.add_rules(pack.policies())
        for event, handler, name, priority in pack.hooks():
            self.register_hook(event, handler, name=name, priority=priority)
        for fragment in pack.system_fragments():
            self.context.add_fragment(fragment)

    def register_workspace_tools(
        self,
        workspace: Path | None = None,
        *,
        sandbox_preference: str | None = None,
    ) -> None:
        """注册内置工作区文件/执行工具（read/write/edit/glob/exec）。"""
        from harness.tools import register_builtin_tools

        register_builtin_tools(
            self.tools,
            workspace or self.config.workspace,
            sandbox_preference=sandbox_preference or self.config.sandbox,
        )
        _sync_tool_risks(self.tools, self.permission)

    async def attach_mcp(self, gateway: Any) -> None:
        """挂载 MCP 网关：connect_all 并把实例保存在 harness.mcp。"""
        self.mcp = gateway
        await gateway.connect_all(self.tools)
        _sync_tool_risks(self.tools, self.permission)

    def session(
        self,
        session_id: str | None = None,
        *,
        workspace: Path | None = None,
        goal: str | None = None,
    ) -> Session:
        """创建会话；可选设置 goal 供 GoalStopPolicy 使用。"""
        sid = session_id or new_session_id()
        state = SessionState(
            session_id=sid,
            workspace=(workspace or self.config.workspace).resolve(),
        )
        if goal:
            state.extras["goal"] = goal
        return Session(state, loop=self.loop, harness=self)

    async def aclose(self) -> None:
        """释放 cron 线程、MCP 连接与队友进程。"""
        if self.cron is not None:
            self.cron.stop()
        if self.team is not None and hasattr(self.team, "shutdown_all"):
            self.team.shutdown_all()
        if self.mcp is not None and hasattr(self.mcp, "close"):
            await self.mcp.close()

    @property
    def packs(self) -> dict[str, CapabilityPack]:
        """已注册能力包只读视图。"""
        return dict(self._packs)


def _install_tools(
    registry: ToolRegistry,
    permission: PermissionGate,
    tools: list[Tool],
) -> None:
    """注册工具并同步风险。"""
    for tool in tools:
        registry.register(tool)
        permission.set_tool_risk(tool.name, tool.risk)


def _sync_tool_risks(registry: ToolRegistry, permission: PermissionGate) -> None:
    """把当前注册表中的风险写进权限门。"""
    for tool in registry.assemble().tools.values():
        permission.set_tool_risk(tool.name, tool.risk)


def _wire_teams(
    cfg: HarnessConfig,
    tools: ToolRegistry,
    permission: PermissionGate,
) -> Any | None:
    """装配进程级 TeamRuntime 与相关工具。"""
    try:
        from harness.teams import Mailbox, TeamRuntime, team_tools

        mailbox = Mailbox(cfg.resolved_mailbox_db())
        team = TeamRuntime(mailbox, lead_name="lead")
        _install_tools(tools, permission, team_tools(team))
        # 读类等待工具默认允许；spawn/shutdown 保持 ASK/规则表
        permission.add_rules(
            [
                PermissionRule(
                    tool_pattern="wait_teammate",
                    decision=PermissionDecision.ALLOW,
                    reason="waiting on mailbox is safe",
                )
            ]
        )
        return team
    except Exception:
        logger.exception("failed to wire teams subsystem")
        return None


def _wire_services(
    cfg: HarnessConfig,
    tools: ToolRegistry,
    hooks: HookBus,
    permission: PermissionGate,
    context: ContextAssembler,
    notifications: NotificationHub,
) -> tuple[Any, Any, Any, Any]:
    """按配置启用 memory / tasks / cron / background；失败时该项为 None。"""
    memory = None
    tasks = None
    cron = None
    background = None

    if cfg.enable_memory:
        try:
            from harness.memory import FileMemoryStore, make_memory_extract_hook, memory_tools

            memory = FileMemoryStore(cfg.resolved_memory_dir())
            context.memory = memory
            _install_tools(tools, permission, memory_tools(memory))
            event, handler, name, priority = make_memory_extract_hook(memory)
            hooks.register(event, handler, name=name, priority=priority)
            # 记忆读工具默认允许
            permission.add_rules(
                [
                    PermissionRule(
                        tool_pattern="read_memory",
                        decision=PermissionDecision.ALLOW,
                        reason="read memory is safe",
                    ),
                    PermissionRule(
                        tool_pattern="list_memories",
                        decision=PermissionDecision.ALLOW,
                        reason="list memories is safe",
                    ),
                ]
            )
        except Exception:
            logger.exception("failed to wire memory subsystem")
            memory = None

    if cfg.enable_tasks:
        try:
            from harness.tasks import BackgroundJobRunner, TaskStore, task_tools

            tasks = TaskStore(cfg.resolved_tasks_db())
            background = BackgroundJobRunner()
            notifications.add_source(background)
            _install_tools(tools, permission, task_tools(tasks))
        except Exception:
            logger.exception("failed to wire tasks subsystem")
            tasks = None
            background = None

    if cfg.enable_cron:
        try:
            from harness.tasks import CronScheduler

            cron = CronScheduler()
            cron.start()
            notifications.add_source(cron)
        except Exception:
            logger.exception("failed to wire cron subsystem")
            cron = None

    return memory, tasks, cron, background
