"""DashScope 重排客户端：调用 qwen3-rerank 对受控候选证据进行相关性评分。

定义 RerankClient、RerankClientError、RerankScore 和 DashScopeRerankClient；客户端只传入
已经过权限校验的查询与文本，不接受模型生成的 SQL、Cypher 或权限条件。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from rail_rag.config.settings import Settings


class RerankClientError(RuntimeError):
    """重排模型不可用、配置缺失或响应不符合协议时抛出。"""


@dataclass(frozen=True)
class RerankScore:
    """一个候选文本在重排模型输出中的位置和相关性分数。"""

    index: int
    relevance_score: float


class RerankClient(Protocol):
    """定义应用层所需的批量重排能力。"""

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> list[RerankScore]:
        """返回候选索引与相关性分数，索引必须对应输入 documents 的位置。"""


class DashScopeRerankClient:
    """通过 DashScope 工作空间兼容接口调用 qwen3-rerank。"""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = (
            str(settings.chat_rerank_base_url).rstrip("/")
            if settings.chat_rerank_base_url is not None
            else None
        )
        self._api_key = settings.chat_rerank_api_key or settings.embedding_api_key
        self._model_name = settings.chat_rerank_model_name
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.chat_rerank_connect_timeout_seconds,
            read=settings.chat_rerank_response_timeout_seconds,
            write=settings.chat_rerank_response_timeout_seconds,
            pool=settings.chat_rerank_connect_timeout_seconds,
        )

    @property
    def configured(self) -> bool:
        """仅当工作空间地址与密钥均存在时允许发起远程请求。"""
        return self._base_url is not None and self._token_or_none() is not None

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> list[RerankScore]:
        """发送一次受限批量重排请求，并严格校验返回的候选索引。"""
        message = query.strip()
        if not message or not documents or top_n <= 0:
            return []
        if top_n > len(documents):
            top_n = len(documents)
        token = self._token_or_none()
        if self._base_url is None or token is None:
            raise RerankClientError("CHAT_RERANK_BASE_URL and API key are required")
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    "/reranks",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": self._model_name,
                        "query": message,
                        "documents": documents,
                        "top_n": top_n,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RerankClientError(
                f"Rerank request failed with HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise RerankClientError(f"Rerank network request failed: {exc}") from exc
        return self._scores_from_payload(response.json(), len(documents))

    def _token_or_none(self) -> str | None:
        """读取可复用的 DashScope 密钥，空值视为未配置。"""
        if self._api_key is None:
            return None
        token = self._api_key.get_secret_value().strip()
        return token or None

    @staticmethod
    def _scores_from_payload(payload: Any, document_count: int) -> list[RerankScore]:
        """解析 DashScope 兼容响应，拒绝重复、越界和非数值分数。"""
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise RerankClientError("Rerank API returned an invalid response")
        parsed: list[RerankScore] = []
        seen_indexes: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            score = item.get("relevance_score")
            if (
                not isinstance(index, int)
                or index < 0
                or index >= document_count
                or index in seen_indexes
                or not isinstance(score, (int, float))
            ):
                continue
            seen_indexes.add(index)
            parsed.append(RerankScore(index=index, relevance_score=float(score)))
        if not parsed:
            raise RerankClientError("Rerank API returned no valid scores")
        return parsed
