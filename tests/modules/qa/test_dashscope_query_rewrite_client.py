"""DashScope 查询改写客户端测试：验证完整提示词模板、模型参数和查询占位符替换。

定义一个异步 MockTransport 测试，确保七组改写示例随请求发送且不会继续使用旧的第四种策略。
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from rail_rag.config.settings import Settings
from rail_rag.modules.qa.infrastructure.dashscope_query_rewrite_client import (
    DashScopeQueryRewriteClient,
)


@pytest.mark.asyncio
async def test_sends_complete_query_rewrite_prompt_template() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Tesla Model Y续航里程"}}]})

    client = DashScopeQueryRewriteClient(
        Settings(embedding_api_key=SecretStr("test-key")),
        transport=httpx.MockTransport(handler),
    )
    result = await client.rewrite("毛豆Y续航多少")

    assert result == "Tesla Model Y续航里程"
    assert captured["model"] == "qwen3.5-flash"
    assert captured["enable_thinking"] is False
    assert captured["stream"] is False
    messages = captured["messages"]
    assert isinstance(messages, list)
    prompt = messages[0]["content"]
    assert prompt.count("Input：") == 7
    assert "用户的原始查询：毛豆Y续航多少" in prompt
    assert "上面是3种改写策略" in prompt
    assert "4、车型信息提取" not in prompt
