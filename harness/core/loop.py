"""
AgentLoop：生产级不变式循环。
"""

from __future__ import annotations

import logging
from typing import Any

from harness.core.context import Compactor, ContextAssembler, NullCompactor
from harness.core.hooks import HookAction, HookBus, HookEvent
from harness.core.model import ModelClient
from harness.core.permission import PermissionDecision, PermissionGate
from harness.core.registry import ToolDispatcher, ToolRegistry
from harness.core.session import SessionState
from harness.core.stop_policy import AllowStopPolicy, StopPolicy
from harness.core.transcript import InMemoryTranscriptStore, TranscriptStore
from harness.core.types import (
    ModelRequest,
    StopAction,
    StopDecision,
    ToolResultBlock,
    TurnResult,
    assistant_from_response,
    user_text,
    user_tool_results,
)

logger = logging.getLogger(__name__)


class AgentLoop:
    """唯一控制流：模型决策，harness 执行。"""

    def __init__(
        self,
        *,
        model: ModelClient,
        tools: ToolRegistry,
        hooks: HookBus | None = None,
        permission: PermissionGate | None = None,
        context: ContextAssembler | None = None,
        compactor: Compactor | None = None,
        stop_policy: StopPolicy | None = None,
        transcript: TranscriptStore | None = None,
        # 通知注入回调：cron / background / mailbox
        notification_injector: Any | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.hooks = hooks or HookBus()
        self.permission = permission or PermissionGate()
        self.context = context or ContextAssembler(base_system="You are a helpful agent.")
        self.compactor = compactor or NullCompactor()
        self.stop_policy = stop_policy or AllowStopPolicy()
        self.transcript = transcript or InMemoryTranscriptStore()
        self.notification_injector = notification_injector

    async def run_turn(self, session: SessionState, user_input: str) -> TurnResult:
        """执行一个用户回合，可能内部多轮 tool 循环。"""
        session.reset_turn_counters()
        session.latest_user_text = user_input

        # --- UserPromptSubmit ---
        await self.hooks.emit(
            HookEvent.USER_PROMPT_SUBMIT,
            {"session": session, "text": user_input},
        )

        user_msg = user_text(user_input)
        session.messages.append(user_msg)
        await self.transcript.append(session.session_id, user_msg)

        final_text = ""
        stop = StopDecision(action=StopAction.ALLOW, reason="init")

        while True:
            # 注入后台 / cron / mailbox 通知
            if self.notification_injector is not None:
                await self.notification_injector(session)

            # 组装上下文并压缩历史
            await self.hooks.emit(HookEvent.BEFORE_MODEL, {"session": session})
            messages = await self.compactor.prepare(list(session.messages))
            system = await self.context.build(session, latest_user_text=session.latest_user_text)
            pool = self.tools.assemble()
            # 同步风险表到权限门（新注册工具即时生效）
            for tool in pool.tools.values():
                self.permission.set_tool_risk(tool.name, tool.risk)

            request = ModelRequest(
                model=self.model.model_id,
                system=system,
                messages=messages,
                tools=pool.specs(),
                max_tokens=self.model.max_tokens,
            )
            session.iteration_count += 1
            response = await self.model.complete(request)

            assistant_msg = assistant_from_response(response)
            session.messages.append(assistant_msg)
            await self.transcript.append(session.session_id, assistant_msg)
            await self.hooks.emit(
                HookEvent.AFTER_MODEL,
                {"session": session, "response": response},
            )

            tool_calls = response.tool_uses
            if not tool_calls:
                # --- Stop 门控 ---
                stop = await self.stop_policy.decide(session, response)
                if stop.action == StopAction.CONTINUE and stop.prompt:
                    cont = user_text(stop.prompt)
                    session.messages.append(cont)
                    await self.transcript.append(session.session_id, cont)
                    continue

                stop_outcome = await self.hooks.emit(
                    HookEvent.STOP,
                    {"session": session, "response": response, "stop": stop},
                )
                # 钩子可强制续跑
                if stop_outcome.action == HookAction.FORCE_CONTINUE:
                    prompt = str(stop_outcome.payload or "Continue.")
                    cont = user_text(prompt)
                    session.messages.append(cont)
                    await self.transcript.append(session.session_id, cont)
                    continue

                final_text = response.text
                break

            # --- 执行工具 ---
            dispatcher = ToolDispatcher(pool)
            result_blocks: list[ToolResultBlock] = []
            for call in tool_calls:
                session.tool_call_count += 1
                pre = await self.hooks.emit(
                    HookEvent.PRE_TOOL_USE,
                    {"session": session, "tool": call},
                )
                if pre.denied:
                    result_blocks.append(
                        ToolResultBlock(
                            tool_use_id=call.id,
                            content=f"Error: denied by hook: {pre.reason}",
                            is_error=True,
                        )
                    )
                    continue

                verdict = await self.permission.authorize(call)
                if verdict.decision != PermissionDecision.ALLOW:
                    result_blocks.append(
                        ToolResultBlock(
                            tool_use_id=call.id,
                            content=f"Error: permission denied: {verdict.reason}",
                            is_error=True,
                        )
                    )
                    continue

                # 钩子若 rewrite，替换 input
                if pre.action == HookAction.REWRITE and isinstance(pre.payload, dict):
                    call.input = pre.payload

                tool_result = await dispatcher.execute(call)
                await self.hooks.emit(
                    HookEvent.POST_TOOL_USE,
                    {"session": session, "tool": call, "result": tool_result},
                )
                result_blocks.append(
                    ToolResultBlock(
                        tool_use_id=call.id,
                        content=tool_result.content,
                        is_error=tool_result.is_error,
                    )
                )

            results_msg = user_tool_results(result_blocks)
            session.messages.append(results_msg)
            await self.transcript.append(session.session_id, results_msg)
            # 继续 while，把结果喂回模型

        return TurnResult(
            session_id=session.session_id,
            final_text=final_text,
            stop=stop,
            tool_call_count=session.tool_call_count,
            message_count=len(session.messages),
        )
