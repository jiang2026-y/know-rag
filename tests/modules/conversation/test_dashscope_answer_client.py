"""DashScope 主回答客户端测试：验证 qwen3.7-plus 的流式请求和增量解析。

定义一项测试，检查请求模型、Token 上限、关闭思考模式及 OpenAI 兼容 SSE 文本与非文本事件解析。
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from rail_rag.config.settings import Settings
from rail_rag.modules.conversation.infrastructure.dashscope_answer_client import (
    DashScopeAnswerClient,
)


@pytest.mark.asyncio
async def test_streams_qwen_answer_deltas_without_thinking() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=(
                'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"，世界"}}]}\n\n'
                'data: {"usage":{"total_tokens":8}}\n\n'
                'data: {"choices":[],"usage":{"total_tokens":8}}\n\n'
                "data: [DONE]\n\n"
            ).encode("utf-8"),
        )

    client = DashScopeAnswerClient(
        Settings(embedding_api_key=SecretStr("test-key")),
        transport=httpx.MockTransport(handler),
    )

    deltas = [
        delta async for delta in client.stream_answer("系统提示", "用户问题")
    ]

    assert deltas == ["你好", "，世界"]
    assert captured["model"] == "qwen3.7-plus"
    assert captured["max_completion_tokens"] == 1024
    assert captured["enable_thinking"] is False
    assert captured["stream"] is True
