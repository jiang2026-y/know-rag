"""DashScope 意图客户端测试：验证模型参数、提示词约束和 JSON 文本提取。

定义 MockTransport 测试，确保调用 qwen3.5-flash 时关闭思考和流式输出。
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from rail_rag.config.settings import Settings
from rail_rag.modules.qa.infrastructure.dashscope_intent_client import DashScopeIntentClient


@pytest.mark.asyncio
async def test_requests_structured_intent_without_thinking_or_streaming() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"related": false, "intent": "闲聊与通用问答", "entities": {}}'
                        }
                    }
                ]
            },
        )

    client = DashScopeIntentClient(
        Settings(embedding_api_key=SecretStr("test-key")),
        transport=httpx.MockTransport(handler),
    )
    result = await client.recognize("今天天气怎么样？", "customer")

    assert json.loads(result)["related"] is False
    assert captured["model"] == "qwen3.5-flash"
    assert captured["enable_thinking"] is False
    assert captured["max_completion_tokens"] == 256
    assert captured["stream"] is False
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "# Few-Shot Examples" in messages[0]["content"]
    assert "用户的原始输入：今天天气怎么样？" in messages[0]["content"]
    assert '"reasoning"' in messages[0]["content"]
