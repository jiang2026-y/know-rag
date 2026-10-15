"""DashScope 重排客户端测试：验证请求协议、响应校验和配置缺失保护。"""

import json

import httpx
import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.qa.infrastructure.dashscope_rerank_client import (
    DashScopeRerankClient,
    RerankClientError,
    RerankScore,
)


@pytest.mark.asyncio
async def test_calls_workspace_rerank_endpoint_and_parses_scores() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/compatible-api/v1/reranks"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload == {
            "model": "qwen3-rerank",
            "query": "车辆保养周期",
            "documents": ["候选一", "候选二"],
            "top_n": 2,
        }
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.4}]},
        )

    client = DashScopeRerankClient(
        Settings(
            chat_rerank_base_url="https://workspace.example/compatible-api/v1",
            chat_rerank_api_key="test-key",
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.configured is True
    assert await client.rerank("车辆保养周期", ["候选一", "候选二"], top_n=2) == [
        RerankScore(index=1, relevance_score=0.9),
        RerankScore(index=0, relevance_score=0.4),
    ]


@pytest.mark.asyncio
async def test_rejects_unconfigured_client_without_network_request() -> None:
    client = DashScopeRerankClient(
        Settings(
            chat_rerank_base_url=None,
            chat_rerank_api_key=None,
            embedding_api_key=None,
        )
    )

    assert client.configured is False
    with pytest.raises(RerankClientError, match="BASE_URL"):
        await client.rerank("问题", ["候选"], top_n=1)
