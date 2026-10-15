"""问答 SSE 事件测试：验证稳定的 event、id 与 JSON data 编码。

定义一项测试，检查 answer_delta 事件会按协议生成客户端可消费的 SSE 文本。
"""

from rail_rag.modules.conversation.domain.stream_event import (
    ChatStreamEvent,
    encode_sse_event,
)


def test_encodes_contractual_sse_event() -> None:
    event = ChatStreamEvent(
        event="answer_delta",
        conversation_id="conversation-1",
        message_id="message-1",
        sequence=1,
        trace_id="trace-1",
        payload={"delta": "你好"},
    )

    encoded = encode_sse_event(event)

    assert encoded.startswith("id: 1\nevent: answer_delta\ndata: ")
    assert '"trace_id":"trace-1"' in encoded
    assert encoded.endswith("\n\n")
