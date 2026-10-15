"""验证 DashScope OpenAI 兼容向量客户端的请求、批处理与响应校验。"""

import httpx
import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.infrastructure.dashscope_embedding_client import (
    DashScopeEmbeddingClient,
    EmbeddingError,
)


@pytest.mark.asyncio
async def test_embeds_texts_in_configured_batches_and_preserves_indexes() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = __import__("json").loads(request.content)
        assert request.headers["authorization"] == "Bearer test-token"
        assert payload["model"] == "text-embedding-v4"
        assert payload["dimensions"] == 3
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index), 0.0, 1.0]}
                    for index, _ in reversed(list(enumerate(payload["input"])))
                ]
            },
        )

    client = DashScopeEmbeddingClient(
        Settings(
            embedding_api_key="test-token",
            embedding_dimensions=3,
            embedding_max_segments_per_batch=2,
        ),
        transport=httpx.MockTransport(handler),
    )

    assert await client.embed_many(["one", "two", "three"]) == [
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 1.0],
        [0.0, 0.0, 1.0],
    ]
    assert [request.url.path for request in requests] == [
        "/compatible-mode/v1/embeddings",
        "/compatible-mode/v1/embeddings",
    ]


@pytest.mark.asyncio
async def test_requires_embedding_api_key() -> None:
    client = DashScopeEmbeddingClient(Settings(embedding_api_key=None))

    with pytest.raises(EmbeddingError, match="EMBEDDING_API_KEY"):
        await client.embed_many(["segment"])


@pytest.mark.asyncio
async def test_rejects_unexpected_vector_dimension() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})

    client = DashScopeEmbeddingClient(
        Settings(embedding_api_key="test-token", embedding_dimensions=3),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EmbeddingError, match="unexpected dimension"):
        await client.embed_many(["segment"])
