"""模型端口：循环只依赖本抽象，不绑定任何厂商协议。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.core.types import ModelRequest, ModelResponse


class ModelClient(ABC):
    """模型客户端端口。

    实现应放在 `agent_harness.models.*`（或业务侧自定义适配器），
    将厂商请求/响应映射为内部 ModelRequest / ModelResponse。

    model_id / max_tokens 属于客户端，不属于 AgentLoop。
    """

    def __init__(self, *, model_id: str, max_tokens: int = 8000) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """完成一次补全。"""
