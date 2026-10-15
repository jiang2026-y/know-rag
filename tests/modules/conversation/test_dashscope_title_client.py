"""验证 DashScope 会话标题客户端的提示词、快速模式参数与输出清理。"""

import json

import httpx
import pytest
from pydantic import SecretStr

from rail_rag.modules.conversation.infrastructure.dashscope_title_client import DashScopeTitleClient
from rail_rag.config.settings import Settings


@pytest.mark.asyncio
async def test_generates_a_short_title_without_thinking() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "\n\"知识库检索方案\"\n"}}]},
        )

    client = DashScopeTitleClient(
        Settings(embedding_api_key=SecretStr("test-key")),
        transport=httpx.MockTransport(handler),
    )

    assert await client.generate("如何实现知识库检索？") == "知识库检索方案"
    assert captured["model"] == "qwen3.5-flash"
    assert captured["enable_thinking"] is False
    assert captured["max_completion_tokens"] == 32
    assert captured["stream"] is False
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "不超过20个字" in messages[0]["content"]
