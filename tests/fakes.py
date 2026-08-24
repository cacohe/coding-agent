"""仅供 pytest 使用的脚本化 ModelClient。业务代码不得依赖本模块。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from harness.core.model import ModelClient
from harness.core.types import (
    ContentBlock,
    ModelRequest,
    ModelResponse,
    TextBlock,
    ToolUseBlock,
)


class FakeModelClient(ModelClient):
    """按队列返回预设响应；也可传入 responder。"""

    def __init__(
        self,
        script: list[ModelResponse] | None = None,
        *,
        model_id: str = "test-model",
        max_tokens: int = 8000,
        responder: Any | None = None,
    ) -> None:
        super().__init__(model_id=model_id, max_tokens=max_tokens)
        self._script = list(script or [])
        self._responder = responder
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if self._responder is not None:
            result = self._responder(request)
            if hasattr(result, "__await__"):
                result = await result
            return result
        if not self._script:
            return ModelResponse(content=[TextBlock(text="(fake model: empty script)")])
        return self._script.pop(0)


def text_response(text: str) -> ModelResponse:
    return ModelResponse(content=[TextBlock(text=text)], stop_reason="end_turn")


def tool_call_response(name: str, input_data: dict[str, Any], *, text: str = "") -> ModelResponse:
    blocks: list[ContentBlock] = []
    if text:
        blocks.append(TextBlock(text=text))
    blocks.append(ToolUseBlock(id=f"call_{uuid4().hex[:8]}", name=name, input=input_data))
    return ModelResponse(content=blocks, stop_reason="tool_use")
