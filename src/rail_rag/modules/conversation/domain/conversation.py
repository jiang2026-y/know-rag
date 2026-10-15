"""对话会话的数据模型：定义新会话请求和立即返回的临时标题结果。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateConversationRequest(BaseModel):
    """创建会话时提交第一条用户消息；身份来自服务端 Session。"""

    first_message: str = Field(min_length=1)


class ConversationCreatedResponse(BaseModel):
    """会话已落库后的同步响应，标题可能仍是临时标题。"""

    conversation_id: str
    first_message_id: str
    title: str
    title_pending: bool = True


class StreamChatMessageRequest(BaseModel):
    """为已有用户消息生成回答，或创建一条后续用户消息并立即回答。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str | None = Field(default=None, min_length=1, max_length=64)
    message: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_source(self) -> "StreamChatMessageRequest":
        """每个回答流恰好对应一条已有或新建的用户消息。"""
        if (self.message_id is None) == (self.message is None):
            raise ValueError("Provide exactly one of message_id or message")
        if self.message is not None and not self.message.strip():
            raise ValueError("Message must not be blank")
        return self
