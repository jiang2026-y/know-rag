"""DashScope 查询路由客户端测试：验证完整提示词、语法修正和模型调用参数。
定义一个异步 MockTransport 测试，确保用户提供的三类路由规则随请求完整发送。
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from rail_rag.config.settings import Settings
from rail_rag.modules.qa.infrastructure.dashscope_query_router_client import (
    DashScopeQueryRouterClient,
)


@pytest.mark.asyncio
async def test_sends_complete_query_router_prompt_template() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
        "intent": "查询车辆关系", "strategies": ["graph_db", "knowledge_base"], "reasoning": "关系查询", "confidence": 0.95,
        })}}]})

    client = DashScopeQueryRouterClient(
        Settings(embedding_api_key=SecretStr("test-key")),
        transport=httpx.MockTransport(handler),
    )
    result = await client.route("纯电车型都有哪些？")

    assert json.loads(result)["strategies"] == ["graph_db", "knowledge_base"]
    assert captured["model"] == "qwen3.5-flash"
    assert captured["enable_thinking"] is False
    messages = captured["messages"]
    assert isinstance(messages, list)
    prompt = messages[0]["content"]
    assert "关系型数据库（Relational DB）" in prompt
    assert "图数据库（Graph DB）" in prompt
    assert "知识库检索适用场景" in prompt
    assert '"strategies": ["relational_db", "knowledge_base"]' in prompt
    assert "用户的原始查询：纯电车型都有哪些？" in prompt
