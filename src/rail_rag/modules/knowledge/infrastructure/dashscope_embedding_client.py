"""DashScope 向量客户端：批量调用 OpenAI 兼容 embeddings 接口。

定义 EmbeddingClient 协议、DashScopeEmbeddingClient 实现和 EmbeddingError 异常，
用于把 Markdown 分片文本转换为指定维度的稠密向量。
"""

from typing import Any, Protocol

import httpx

from rail_rag.config.settings import Settings


class EmbeddingError(RuntimeError):
    """已配置的向量模型服务无法返回向量时抛出。"""


class EmbeddingClient(Protocol):
    """向量存储使用的 Embedding 服务协议。"""

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """按输入文本顺序返回一一对应的稠密向量。"""


class DashScopeEmbeddingClient:

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = str(settings.embedding_base_url).rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model_name = settings.embedding_model_name
        self._dimensions = settings.embedding_dimensions
        self._batch_size = settings.embedding_max_segments_per_batch
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.embedding_connect_timeout_seconds,
            read=settings.embedding_response_timeout_seconds,
            write=settings.embedding_response_timeout_seconds,
            pool=settings.embedding_connect_timeout_seconds,
        )

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        token = self._api_token()
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start : start + self._batch_size]
                # 模型服务有单次输入数量限制；分批后依次追加，保持原始文本顺序不变。
                vectors.extend(await self._embed_batch(client, token, batch))
        return vectors

    def _api_token(self) -> str:
        if self._api_key is None:
            raise EmbeddingError("EMBEDDING_API_KEY is required for vector indexing")
        token = self._api_key.get_secret_value().strip()
        if not token:
            raise EmbeddingError("EMBEDDING_API_KEY is required for vector indexing")
        return token

    async def _embed_batch(
        self, client: httpx.AsyncClient, token: str, texts: list[str]
    ) -> list[list[float]]:
        try:
            response = await client.post(
                "/embeddings",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": self._model_name,
                    "input": texts,
                    "dimensions": self._dimensions,
                    "encoding_format": "float",
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:1000]
            raise EmbeddingError(
                f"Embedding request failed with HTTP {exc.response.status_code}: {detail or exc.response.reason_phrase}"
            ) from exc
        except httpx.RequestError as exc:
            raise EmbeddingError(f"Embedding network request failed: {exc}") from exc
        return self._vectors_from_payload(response.json(), len(texts))
    #从Embedding API的原始响应中提取所有向量，并确保返回的向量列表顺序与原始输入文本顺序完全一致。
    def _vectors_from_payload(self, payload: Any, expected_count: int) -> list[list[float]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise EmbeddingError("Embedding API returned an invalid response")
        ordered: list[list[float] | None] = [None] * expected_count
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise EmbeddingError("Embedding API returned an invalid vector item")
            index = item.get("index")
            vector = item.get("embedding")
            if not isinstance(index, int) or not 0 <= index < expected_count:
                raise EmbeddingError("Embedding API returned an invalid vector index")
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                raise EmbeddingError(
                    f"Embedding API returned a vector with an unexpected dimension; expected {self._dimensions}"
                )
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in vector):
                raise EmbeddingError("Embedding API returned a non-numeric vector")
            # 服务可能按任意顺序返回 data；必须依据 index 还原，避免文本与向量错配。
            ordered[index] = [float(value) for value in vector]
        if any(vector is None for vector in ordered):
            raise EmbeddingError("Embedding API did not return vectors for every input")
        return [vector for vector in ordered if vector is not None]
