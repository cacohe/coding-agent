"""统一消息与工具协议。

与 Anthropic Messages API 的 content block 形状对齐，经 ModelPort 映射到各厂商。
所有包只依赖本模块中的类型，避免循环直接耦合具体实现。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    """工具风险分级，供 PermissionGate 做 allow / ask / deny 决策。"""

    READ = "read"  # 只读：读文件、列目录、检索
    WRITE = "write"  # 写盘：创建/修改/删除工作区内文件
    EXEC = "exec"  # 执行：子进程 / shell
    NETWORK = "network"  # 出网：HTTP、MCP 远程等


class TextBlock(BaseModel):
    """纯文本内容块。"""

    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """模型发出的工具调用请求。"""

    type: Literal["tool_use"] = "tool_use"
    id: str  # 与后续 tool_result.tool_use_id 一一对应
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    """工具执行结果，必须以 user 消息形式回写给模型。"""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


# 会话中可出现的全部内容块类型
ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


class Message(BaseModel):
    """一轮对话消息（user / assistant）。"""

    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock] | str

    def text(self) -> str:
        """提取纯文本视图，便于日志与记忆选择器使用。"""
        if isinstance(self.content, str):
            return self.content
        parts: list[str] = []
        for block in self.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
            elif isinstance(block, ToolResultBlock):
                parts.append(block.content)
        return "\n".join(parts)


class ToolSpec(BaseModel):
    """暴露给模型的工具 schema（不含执行逻辑）。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel = RiskLevel.READ  # 默认只读，写/执行工具必须显式声明


class ToolResult(BaseModel):
    """工具处理器返回值。"""

    content: str
    is_error: bool = False
    # 可选结构化产物（路径、任务 id 等），供 host / 审计使用，不直接进模型
    artifacts: dict[str, Any] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    """发给 ModelPort 的统一请求。"""

    model: str
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int = 8000
    temperature: float | None = None


class ModelResponse(BaseModel):
    """ModelPort 的统一响应。"""

    content: list[ContentBlock]
    stop_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        """取出本轮所有 tool_use，供循环决定是否继续。"""
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def text(self) -> str:
        """拼接本轮文本块，供最终回复展示。"""
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))


class StopAction(str, Enum):
    """StopPolicy 在「模型未再调工具」时的裁决。"""

    ALLOW = "allow"  # 正常结束本轮
    CONTINUE = "continue"  # 注入续写提示，继续循环
    FAIL = "fail"  # 判定目标失败，结束并向 host 报告
    LIMIT = "limit"  # 触达上限，结束


class StopDecision(BaseModel):
    """Stop 边界的裁决结果。"""

    action: StopAction = StopAction.ALLOW
    reason: str = ""
    # CONTINUE 时注入的用户提示；其它动作可忽略
    prompt: str | None = None


class TurnResult(BaseModel):
    """Session.run 结束后返回给 host 的摘要。"""

    session_id: str
    final_text: str
    stop: StopDecision
    # 本轮累计工具调用次数，便于观测与限流
    tool_call_count: int = 0
    # 原始 messages 快照长度（不含完整拷贝，避免过大响应）
    message_count: int = 0


def user_text(text: str) -> Message:
    """构造一条纯文本 user 消息。"""
    return Message(role="user", content=[TextBlock(text=text)])


def assistant_from_response(response: ModelResponse) -> Message:
    """把模型响应包装成 assistant 消息写入 transcript。"""
    return Message(role="assistant", content=list(response.content))


def user_tool_results(results: list[ToolResultBlock]) -> Message:
    """把一批 tool_result 合成一条 user 消息（Anthropic 约定）。"""
    return Message(role="user", content=list(results))
