"""LiteLLM 适配器。

core 只认识 ModelRequest / ModelResponse。
LiteLLM.acompletion 的入参/出参沿用其统一 chat 协议（与 OpenAI Chat Completions 同形），
因此本文件负责双向转换——这是适配器的本职，不是让业务或 core 绑 OpenAI。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from harness.core.model import ModelClient
from harness.core.types import (
    ContentBlock,
    Message,
    ModelRequest,
    ModelResponse,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


class LiteLLMModelClient(ModelClient):
    """经 LiteLLM 调用多厂商模型。

    - api_key / api_base 显式传入时覆盖环境变量（统一网关）
    - 均为 None 时由 LiteLLM 按 model 前缀读厂商标准环境变量
    """

    def __init__(
        self,
        *,
        model_id: str,
        max_tokens: int = 8000,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(model_id=model_id, max_tokens=max_tokens)
        self.api_base = api_base
        self.api_key = api_key

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            import litellm
        except ImportError as exc:
            raise RuntimeError(
                "litellm is not installed; pass a ModelClient or add litellm dependency"
            ) from exc

        kwargs: dict[str, Any] = {
            "model": request.model or self.model_id,
            "messages": _to_litellm_messages(request.system, request.messages),
            "max_tokens": request.max_tokens or self.max_tokens,
        }
        tools = [_to_litellm_tool(t) for t in request.tools]
        if tools:
            kwargs["tools"] = tools
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        response = await litellm.acompletion(**kwargs)
        return _from_litellm_response(response)


def _to_litellm_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    """内部 Message → litellm.acompletion 所需的 messages 列表。"""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        if isinstance(msg.content, str):
            out.append({"role": msg.role, "content": msg.content})
            continue
        if msg.role == "user" and any(isinstance(b, ToolResultBlock) for b in msg.content):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.tool_use_id,
                            "content": block.content,
                        }
                    )
                elif isinstance(block, TextBlock):
                    out.append({"role": "user", "content": block.text})
            continue
        if msg.role == "assistant":
            text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            tool_calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {
                        "name": b.name,
                        "arguments": json.dumps(b.input, ensure_ascii=False),
                    },
                }
                for b in msg.content
                if isinstance(b, ToolUseBlock)
            ]
            item: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts) or None,
            }
            if tool_calls:
                item["tool_calls"] = tool_calls
            out.append(item)
            continue
        out.append({"role": msg.role, "content": msg.text()})
    return out


def _to_litellm_tool(spec: ToolSpec) -> dict[str, Any]:
    """内部 ToolSpec → litellm tools[] 单项。"""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


def _from_litellm_response(response: Any) -> ModelResponse:
    """litellm 返回值 → 内部 ModelResponse。"""
    choice = response.choices[0]
    message = choice.message
    blocks: list[ContentBlock] = []
    if message.content:
        blocks.append(TextBlock(text=message.content))
    for call in message.tool_calls or []:
        args_raw = call.function.arguments or "{}"
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        blocks.append(
            ToolUseBlock(
                id=call.id,
                name=call.function.name,
                input=args if isinstance(args, dict) else {"value": args},
            )
        )
    usage: dict[str, int] = {}
    if getattr(response, "usage", None):
        usage = {
            "prompt_tokens": int(getattr(response.usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(response.usage, "completion_tokens", 0) or 0),
        }
    return ModelResponse(
        content=blocks,
        stop_reason=getattr(choice, "finish_reason", None),
        usage=usage,
    )
