"""Elasticsearch 向量存储：创建索引并写入可检索的 Markdown 子分片。

定义 ElasticsearchEmbeddingStore 和 ElasticsearchEmbeddingStoreError；服务会确保
``know-engine`` 向量索引存在、删除同一文档的旧向量，再批量写入新向量。
"""

import json
import asyncio
from typing import Any
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.infrastructure.dashscope_embedding_client import (
    DashScopeEmbeddingClient,
    EmbeddingClient,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import StoredKnowledgeSegment


class ElasticsearchEmbeddingStoreError(RuntimeError):
    """Elasticsearch 无法持久化文档向量时抛出。"""


def trust_env_for_host(host: str) -> bool:
    """判断访问该 ES 地址时是否读取系统代理；本地回环地址必须直连。"""
    hostname = (urlsplit(host).hostname or "").strip().lower()
    return hostname not in {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class RetrievedEmbeddingSegment:
    """Elasticsearch 命中的可检索子分片及其权限元数据。"""

    chunk_id: str
    text: str
    metadata: dict[str, Any]
    score: float
    retrieval_methods: tuple[str, ...] = ()
    raw_scores: dict[str, float] = field(default_factory=dict)
    rrf_score: float | None = None


class ElasticsearchEmbeddingStore:
    """使用余弦相似度向量索引持久化可检索子分段。"""

    def __init__(
        self,
        settings: Settings,
        *,
        embedder: EmbeddingClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._host = str(settings.elasticsearch_host).rstrip("/")
        self._trust_env = trust_env_for_host(self._host)
        self._index = settings.elasticsearch_index
        self._embedder = embedder or DashScopeEmbeddingClient(settings)
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.elasticsearch_connect_timeout_seconds,
            read=settings.elasticsearch_response_timeout_seconds,
            write=settings.elasticsearch_response_timeout_seconds,
            pool=settings.elasticsearch_connect_timeout_seconds,
        )

    async def reset_document(self, document_id: int) -> None:
        """在完整重新向量化前删除该文档的旧向量。"""
        try:
            async with httpx.AsyncClient(
                base_url=self._host,
                timeout=self._timeout,
                transport=self._transport,
                trust_env=self._trust_env,
            ) as client:
                await self._ensure_index(client)
                await self._delete_document_vectors(client, document_id)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:1000]
            raise ElasticsearchEmbeddingStoreError(
                f"Elasticsearch request failed with HTTP {exc.response.status_code}: {detail or exc.response.reason_phrase}"
            ) from exc
        except httpx.RequestError as exc:
            raise ElasticsearchEmbeddingStoreError(
                f"Elasticsearch network request failed: {exc}"
            ) from exc

    async def store_segments(
        self, segments: list[StoredKnowledgeSegment]
    ) -> list[tuple[int, str]]:
        """向量化并批量写入一页 MySQL 分片，返回分片与 ES ID 对应关系。"""
        if not segments:
            return []
        try:
            vectors = await self._embedder.embed_many([segment.text for segment in segments])
            if len(vectors) != len(segments):
                raise ElasticsearchEmbeddingStoreError(
                    "Embedding provider did not return a vector for every chunk"
                )
            async with httpx.AsyncClient(
                base_url=self._host,
                timeout=self._timeout,
                transport=self._transport,
                trust_env=self._trust_env,
            ) as client:
                # 只有模型返回数量与分段数量完全一致时才批量写入，
                # 防止分段与向量错位后污染索引。
                await self._bulk_index(client, segments, vectors)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:1000]
            raise ElasticsearchEmbeddingStoreError(
                f"Elasticsearch request failed with HTTP {exc.response.status_code}: {detail or exc.response.reason_phrase}"
            ) from exc
        except httpx.RequestError as exc:
            raise ElasticsearchEmbeddingStoreError(
                f"Elasticsearch network request failed: {exc}"
            ) from exc
        return [(segment.id, segment.chunk_id) for segment in segments]

    async def search_segments(
        self, question: str, *, accessible_by: list[str], limit: int
    ) -> list[RetrievedEmbeddingSegment]:
        """向量化问题后，仅在允许的可见范围内执行 KNN 检索。"""
        if not question.strip() or not accessible_by or limit <= 0:
            return []
        try:
            vectors = await self._embedder.embed_many([question.strip()])
            if len(vectors) != 1:
                raise ElasticsearchEmbeddingStoreError("Embedding provider returned no query vector")
            async with httpx.AsyncClient(
                base_url=self._host,
                timeout=self._timeout,
                transport=self._transport,
                trust_env=self._trust_env,
            ) as client:
                await self._ensure_index(client)
                response = await client.post(
                    f"/{self._index}/_search",
                    json={
                        "size": limit,
                        "knn": {
                            "field": "vector",
                            "query_vector": vectors[0],
                            "k": limit,
                            "num_candidates": max(50, limit * 10),
                            "filter": {"terms": {"metadata.accessibleBy": accessible_by}},
                        },
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ElasticsearchEmbeddingStoreError(
                f"Elasticsearch request failed with HTTP {exc.response.status_code}: {exc.response.text.strip()[:1000]}"
            ) from exc
        except httpx.RequestError as exc:
            raise ElasticsearchEmbeddingStoreError(f"Elasticsearch network request failed: {exc}") from exc
        hits = response.json().get("hits", {}).get("hits", [])
        result: list[RetrievedEmbeddingSegment] = []
        for hit in hits:
            source = hit.get("_source", {}) if isinstance(hit, dict) else {}
            metadata = source.get("metadata", {}) if isinstance(source, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            text = source.get("text") if isinstance(source, dict) else None
            chunk_id = hit.get("_id") if isinstance(hit, dict) else None
            score = hit.get("_score") if isinstance(hit, dict) else None
            if isinstance(chunk_id, str) and isinstance(text, str) and isinstance(score, (int, float)):
                result.append(
                    RetrievedEmbeddingSegment(
                        chunk_id,
                        text,
                        metadata,
                        float(score),
                        retrieval_methods=("vector",),
                        raw_scores={"vector": float(score)},
                    )
                )
        return result

    async def search_hybrid_segments(
        self,
        question: str,
        *,
        accessible_by: list[str],
        vector_limit: int,
        full_text_limit: int,
        rrf_k: int,
    ) -> list[RetrievedEmbeddingSegment]:
        """以相同权限过滤并行执行向量、全文召回，再使用 RRF 融合结果。"""
        message = question.strip()
        if not message or not accessible_by or (vector_limit <= 0 and full_text_limit <= 0):
            return []
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        try:
            async with httpx.AsyncClient(
                base_url=self._host,
                timeout=self._timeout,
                transport=self._transport,
                trust_env=self._trust_env,
            ) as client:
                await self._ensure_index(client)
                full_text_task = (
                    asyncio.create_task(
                        self._full_text_search(client, message, accessible_by, full_text_limit)
                    )
                    if full_text_limit > 0
                    else None
                )
                vector_task: asyncio.Task[list[dict[str, Any]]] | None = None
                if vector_limit > 0:
                    try:
                        vectors = await self._embedder.embed_many([message])
                        if len(vectors) != 1:
                            raise ElasticsearchEmbeddingStoreError(
                                "Embedding provider returned no query vector"
                            )
                        vector_task = asyncio.create_task(
                            self._vector_search(client, vectors[0], accessible_by, vector_limit)
                        )
                    except Exception:
                        # 全文检索仍可用时不能因向量服务故障中断问答；两路均失败时统一抛错。
                        vector_task = None
                tasks = [task for task in (vector_task, full_text_task) if task is not None]
                if not tasks:
                    return []
                settled = await asyncio.gather(*tasks, return_exceptions=True)
        except httpx.HTTPStatusError as exc:
            raise ElasticsearchEmbeddingStoreError(
                f"Elasticsearch request failed with HTTP {exc.response.status_code}: {exc.response.text.strip()[:1000]}"
            ) from exc
        except httpx.RequestError as exc:
            raise ElasticsearchEmbeddingStoreError(f"Elasticsearch network request failed: {exc}") from exc

        vector_hits: list[dict[str, Any]] = []
        full_text_hits: list[dict[str, Any]] = []
        task_results = iter(settled)
        if vector_task is not None:
            value = next(task_results)
            if not isinstance(value, Exception):
                vector_hits = value
        if full_text_task is not None:
            value = next(task_results)
            if not isinstance(value, Exception):
                full_text_hits = value
        if not vector_hits and not full_text_hits:
            errors = [value for value in settled if isinstance(value, Exception)]
            if errors:
                error = errors[0]
                if isinstance(error, ElasticsearchEmbeddingStoreError):
                    raise error
                raise ElasticsearchEmbeddingStoreError("Elasticsearch hybrid retrieval failed") from error
            return []
        return self._rrf_merge(vector_hits, full_text_hits, rrf_k)

    async def _vector_search(
        self,
        client: httpx.AsyncClient,
        vector: list[float],
        accessible_by: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """执行权限受限的 KNN 子查询并返回原始命中列表。"""
        response = await client.post(
            f"/{self._index}/_search",
            json={
                "size": limit,
                "knn": {
                    "field": "vector",
                    "query_vector": vector,
                    "k": limit,
                    "num_candidates": max(50, limit * 10),
                    "filter": {"terms": {"metadata.accessibleBy": accessible_by}},
                },
            },
        )
        response.raise_for_status()
        return self._hits_from_payload(response.json())

    async def _full_text_search(
        self,
        client: httpx.AsyncClient,
        question: str,
        accessible_by: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """执行权限受限的 BM25 全文子查询并返回原始命中列表。"""
        response = await client.post(
            f"/{self._index}/_search",
            json={
                "size": limit,
                "query": {
                    "bool": {
                        "filter": [{"terms": {"metadata.accessibleBy": accessible_by}}],
                        "must": [
                        {
                            "match": {
                                "text": {
                                    "query": question,
                                    "analyzer": self._settings.elasticsearch_text_analyzer,
                                }
                            }
                        }
                        ],
                    }
                },
            },
        )
        response.raise_for_status()
        return self._hits_from_payload(response.json())

    @staticmethod
    def _hits_from_payload(payload: Any) -> list[dict[str, Any]]:
        """从 Elasticsearch 响应中提取可供融合的命中记录。"""
        if not isinstance(payload, dict):
            return []
        hits = payload.get("hits", {}).get("hits", [])
        return [hit for hit in hits if isinstance(hit, dict)] if isinstance(hits, list) else []

    @staticmethod
    def _rrf_merge(
        vector_hits: list[dict[str, Any]],
        full_text_hits: list[dict[str, Any]],
        rrf_k: int,
    ) -> list[RetrievedEmbeddingSegment]:
        """按 reciprocal-rank fusion 合并双路命中，并保留每路原始分数。"""
        merged: dict[str, dict[str, Any]] = {}
        for method, hits in (("vector", vector_hits), ("full_text", full_text_hits)):
            for rank, hit in enumerate(hits, start=1):
                chunk_id = hit.get("_id")
                source = hit.get("_source", {})
                score = hit.get("_score")
                if not isinstance(chunk_id, str) or not isinstance(source, dict) or not isinstance(score, (int, float)):
                    continue
                text = source.get("text")
                metadata = source.get("metadata", {})
                if not isinstance(text, str):
                    continue
                if not isinstance(metadata, dict):
                    metadata = {}
                item = merged.setdefault(
                    chunk_id,
                    {"text": text, "metadata": metadata, "raw_scores": {}, "methods": [], "rrf": 0.0},
                )
                item["raw_scores"][method] = float(score)
                item["methods"].append(method)
                item["rrf"] += 1.0 / (rrf_k + rank)
        results = [
            RetrievedEmbeddingSegment(
                chunk_id=chunk_id,
                text=item["text"],
                metadata=item["metadata"],
                score=float(item["rrf"]),
                retrieval_methods=tuple(item["methods"]),
                raw_scores=dict(item["raw_scores"]),
                rrf_score=float(item["rrf"]),
            )
            for chunk_id, item in merged.items()
        ]
        return sorted(results, key=lambda item: (-item.score, item.chunk_id))

    async def _ensure_index(self, client: httpx.AsyncClient) -> None:
        exists = await client.head(f"/{self._index}")
        if exists.status_code == 200:
            await self._ensure_permission_mapping(client)
            return
        if exists.status_code != 404:
            exists.raise_for_status()
        # 首次写入时创建索引；并发创建出现“已存在”错误时按成功处理。
        response = await client.put(f"/{self._index}", json=self._index_definition())
        if response.status_code == 400 and "resource_already_exists_exception" in response.text:
            return
        response.raise_for_status()
        await self._ensure_permission_mapping(client)

    async def _ensure_permission_mapping(self, client: httpx.AsyncClient) -> None:
        """确保文档可见范围按 keyword 建索引，以便权限过滤精确匹配。"""
        response = await client.put(
            f"/{self._index}/_mapping",
            json={"properties": {"metadata": {"properties": {"accessibleBy": {"type": "keyword"}}}}},
        )
        response.raise_for_status()

    async def _delete_document_vectors(
        self, client: httpx.AsyncClient, document_id: int
    ) -> None:
        response = await client.post(
            f"/{self._index}/_delete_by_query?refresh=true",
            json={"query": {"term": {"metadata.docId": document_id}}},
        )
        response.raise_for_status()

    async def _bulk_index(
        self,
        client: httpx.AsyncClient,
        chunks: list[StoredKnowledgeSegment],
        vectors: list[list[float]],
    ) -> None:
        lines: list[str] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            # Bulk API 使用两行一组的 NDJSON：第一行描述索引操作，第二行才是文档内容。
            lines.append(json.dumps({"index": {"_index": self._index, "_id": chunk.chunk_id}}))
            lines.append(
                json.dumps(
                    {"text": chunk.text, "vector": vector, "metadata": chunk.metadata},
                    ensure_ascii=False,
                )
            )
        response = await client.post(
            "/_bulk?refresh=true",
            content="\n".join(lines) + "\n",
            headers={"Content-Type": "application/x-ndjson"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errors") is not False:
            raise ElasticsearchEmbeddingStoreError("Elasticsearch bulk vector indexing failed")

    def _index_definition(self) -> dict[str, Any]:
        return {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "properties": {
                    "text": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "docId": {"type": "long"},
                            "chunkId": {"type": "keyword"},
                            "parentChunkId": {"type": "keyword"},
                            "accessibleBy": {"type": "keyword"},
                        },
                    },
                    "vector": {
                        "type": "dense_vector",
                        "dims": self._settings.embedding_dimensions,
                        "index": True,
                        "similarity": "cosine",
                        "index_options": {"type": "int8_hnsw", "m": 16, "ef_construction": 100},
                    },
                }
            },
        }
