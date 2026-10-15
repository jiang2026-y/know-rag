"""问答 SSE 领域事件：定义进度、查询改写、回答和完成事件与 JSON 编码函数。

定义 ChatStreamEvent 和 encode_sse_event；前者表达事件数据，后者生成符合 SSE 协议的
event、id 与 data 文本，不允许 Controller 自行拼接不一致的事件格式。
"""

from dataclasses import dataclass
import json
from typing import Any, Literal


ChatStreamEventType = Literal[
    "progress", "query_rewrite", "answer_delta", "citation", "error", "done"
]


@dataclass(frozen=True)
class ChatStreamEvent:
    """一条包含会话、问题、顺序号及追踪标识的 SSE 领域事件。"""

    event: ChatStreamEventType
    conversation_id: str
    message_id: str
    sequence: int
    trace_id: str
    payload: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        """将公共事件字段与特定事件负载合并为契约定义的 data JSON。"""
        return {
            "event": self.event,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "sequence": self.sequence,
            "trace_id": self.trace_id,
            **self.payload,
        }


def encode_sse_event(event: ChatStreamEvent) -> str:
    """编码一条 SSE 事件；sequence 同时作为重连语义中的事件 ID。"""
    data = json.dumps(event.as_payload(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event}\ndata: {data}\n\n"
