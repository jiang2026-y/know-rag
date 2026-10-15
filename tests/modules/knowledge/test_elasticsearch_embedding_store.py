"""验证 Elasticsearch 向量索引创建、替换与批量写入流程。"""

import json

import httpx
import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import StoredKnowledgeSegment
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    ElasticsearchEmbeddingStore,
    trust_env_for_host,
)


class FakeEmbedder:
    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[float(index), 0.0, 1.0] for index, _ in enumerate(texts)]


def test_loopback_elasticsearch_hosts_bypass_system_proxy() -> None:
    """本地 ES 必须直连，不能把 127.0.0.1 交给系统代理。"""
    assert trust_env_for_host("http://127.0.0.1:9200") is False
    assert trust_env_for_host("http://localhost:9200") is False
    assert trust_env_for_host("http://[::1]:9200") is False
    assert trust_env_for_host("https://es.example.com") is True


@pytest.mark.asyncio
async def test_creates_index_clears_document_and_stores_one_page() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "HEAD":
            return httpx.Response(404)
        if request.method == "PUT":
            definition = json.loads(request.content)
            if request.url.path.endswith("/_mapping"):
                assert definition["properties"]["metadata"]["properties"]["accessibleBy"]["type"] == "keyword"
                return httpx.Response(200, json={"acknowledged": True})
            assert definition["mappings"]["properties"]["vector"]["dims"] == 3
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path.endswith("/_delete_by_query"):
            assert json.loads(request.content)["query"]["term"]["metadata.docId"] == 7
            return httpx.Response(200, json={"deleted": 0})
        if request.url.path == "/_bulk":
            lines = request.content.decode("utf-8").strip().splitlines()
            assert len(lines) == 2
            assert json.loads(lines[0])["index"]["_id"] == "child"
            assert json.loads(lines[1])["vector"] == [0.0, 0.0, 1.0]
            return httpx.Response(200, json={"errors": False})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    store = ElasticsearchEmbeddingStore(
        Settings(elasticsearch_index="know-engine", embedding_dimensions=3),
        embedder=FakeEmbedder(),
        transport=httpx.MockTransport(handler),
    )
    segments = [
        StoredKnowledgeSegment(
            id=11,
            chunk_id="child",
            text="searchable text",
            metadata={"docId": 7, "parentChunkId": "parent"},
        )
    ]

    await store.reset_document(7)
    assert await store.store_segments(segments) == [(11, "child")]
    assert [request.method for request in requests] == ["HEAD", "PUT", "PUT", "POST", "POST"]


@pytest.mark.asyncio
async def test_search_filters_by_server_calculated_access_labels() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        if request.method == "PUT":
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path.endswith("/_search"):
            payload = json.loads(request.content)
            assert payload["knn"]["filter"] == {
                "terms": {"metadata.accessibleBy": ["VISITOR", "STAFF", "LEADER"]}
            }
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "hits": [
                            {
                                "_id": "chunk-1",
                                "_score": 0.8,
                                "_source": {
                                    "text": "领导可见的流程",
                                    "metadata": {"docId": 7, "accessibleBy": "LEADER"},
                                },
                            }
                        ]
                    }
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    store = ElasticsearchEmbeddingStore(
        Settings(elasticsearch_index="know-engine", embedding_dimensions=3),
        embedder=FakeEmbedder(),
        transport=httpx.MockTransport(handler),
    )

    results = await store.search_segments(
        "领导流程", accessible_by=["VISITOR", "STAFF", "LEADER"], limit=5
    )

    assert results[0].chunk_id == "chunk-1"
    assert results[0].metadata["accessibleBy"] == "LEADER"


@pytest.mark.asyncio
async def test_hybrid_search_uses_identical_access_filter_and_rrf_merges_hits() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        if request.method == "PUT":
            return httpx.Response(200, json={"acknowledged": True})
        if request.url.path.endswith("/_search"):
            payload = json.loads(request.content)
            if "knn" in payload:
                assert payload["knn"]["filter"] == {
                    "terms": {"metadata.accessibleBy": ["VISITOR", "STAFF"]}
                }
                return httpx.Response(
                    200,
                    json={
                        "hits": {
                            "hits": [
                                {"_id": "shared", "_score": 0.91, "_source": {"text": "共同命中", "metadata": {}}},
                                {"_id": "vector-only", "_score": 0.8, "_source": {"text": "向量命中", "metadata": {}}},
                            ]
                        }
                    },
                )
            assert payload["query"]["bool"]["filter"] == [
                {"terms": {"metadata.accessibleBy": ["VISITOR", "STAFF"]}}
            ]
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "hits": [
                            {"_id": "shared", "_score": 4.2, "_source": {"text": "共同命中", "metadata": {}}},
                            {"_id": "text-only", "_score": 3.1, "_source": {"text": "全文命中", "metadata": {}}},
                        ]
                    }
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    store = ElasticsearchEmbeddingStore(
        Settings(elasticsearch_index="know-engine", embedding_dimensions=3),
        embedder=FakeEmbedder(),
        transport=httpx.MockTransport(handler),
    )

    results = await store.search_hybrid_segments(
        "交付检查",
        accessible_by=["VISITOR", "STAFF"],
        vector_limit=20,
        full_text_limit=20,
        rrf_k=60,
    )

    assert [item.chunk_id for item in results] == ["shared", "text-only", "vector-only"]
    assert results[0].retrieval_methods == ("vector", "full_text")
    assert results[0].raw_scores == {"vector": 0.91, "full_text": 4.2}
    assert results[0].rrf_score == pytest.approx(2 / 61)
