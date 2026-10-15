"""检索上下文扩展测试：验证二次授权、父块三级缓存、短父块回溯与长父块相邻窗口补全。

定义内存分片仓储、正文缓存替身及六项测试，覆盖过期 ES 命中丢弃、完整短父块恢复、
长父块自适应相邻子块补全，以及缓存命中不得绕过二次授权。
"""

import pytest

from rail_rag.modules.knowledge.application.context_expansion_service import (
    ContextExpansionService,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    RetrievedEmbeddingSegment,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    AuthorizedKnowledgeSegment,
)


def segment(
    chunk_id: str,
    text: str,
    order: int,
    *,
    parent_chunk_id: str | None = None,
) -> AuthorizedKnowledgeSegment:
    """构造已二次授权的测试分片。"""
    metadata = {"parentChunkId": parent_chunk_id} if parent_chunk_id else {}
    return AuthorizedKnowledgeSegment(chunk_id, text, metadata, 7, 1, order)


class FakeSegmentRepository:
    """按请求分片 ID 返回预置的当前授权分片。"""

    def __init__(self, segments: list[AuthorizedKnowledgeSegment]) -> None:
        self.segments = {item.chunk_id: item for item in segments}
        self.requests: list[list[str]] = []
        self.text_requests: list[list[str]] = []

    async def get_authorized_refs_by_chunk_ids(self, chunk_ids, accessible_by):
        self.requests.append(list(chunk_ids))
        return [self.segments[chunk_id] for chunk_id in chunk_ids if chunk_id in self.segments]

    async def get_text_by_chunk_ids(self, chunk_ids):
        self.text_requests.append(list(chunk_ids))
        return {
            chunk_id: self.segments[chunk_id].text
            for chunk_id in chunk_ids
            if chunk_id in self.segments
        }

    async def list_authorized_headers(self, document_id, document_version, accessible_by):
        return list(self.segments.values())


class FakeTextCache:
    """内存版父块正文缓存，用于区分 L1/L2 命中与 MySQL 回源。"""

    def __init__(self, seed: dict[str, str] | None = None) -> None:
        self.values = dict(seed or {})
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []

    async def get_text(self, chunk_id: str) -> str | None:
        self.reads.append(chunk_id)
        return self.values.get(chunk_id)

    async def set_text(self, chunk_id: str, text: str, *, document_id=None) -> None:
        self.values[chunk_id] = text
        self.writes.append((chunk_id, text))


@pytest.mark.asyncio
async def test_drops_es_hit_that_is_no_longer_authorized() -> None:
    service = ContextExpansionService(FakeSegmentRepository([]))  # type: ignore[arg-type]

    contexts = await service.expand(
        [RetrievedEmbeddingSegment("old-chunk", "旧索引", {"docId": 7}, 0.9)],
        ["VISITOR"],
    )

    assert contexts == []


@pytest.mark.asyncio
async def test_replaces_child_hit_with_complete_short_parent() -> None:
    repository = FakeSegmentRepository(
        [
            segment("parent", "完整章节内容", 1),
            segment("child", "章节片段", 2, parent_chunk_id="parent"),
        ]
    )
    service = ContextExpansionService(repository)  # type: ignore[arg-type]

    contexts = await service.expand(
        [RetrievedEmbeddingSegment("child", "旧片段", {"docId": 7}, 0.9)],
        ["STAFF"],
    )

    assert contexts[0].chunk_id == "child"
    assert contexts[0].text == "完整章节内容"
    assert repository.requests == [["child"], ["parent"]]


@pytest.mark.asyncio
async def test_long_parent_uses_hit_and_adaptive_neighbor_window() -> None:
    long_parent = "父块" * 2500
    repository = FakeSegmentRepository(
        [
            segment("parent", long_parent, 1),
            segment("child-1", "前文。", 2, parent_chunk_id="parent"),
            segment("child-2", "命中内容未结束", 3, parent_chunk_id="parent"),
            segment("child-3", "继续说明未结束", 4, parent_chunk_id="parent"),
            segment("child-4", "结束。", 5, parent_chunk_id="parent"),
        ]
    )
    service = ContextExpansionService(repository)  # type: ignore[arg-type]

    contexts = await service.expand(
        [RetrievedEmbeddingSegment("child-2", "旧片段", {"docId": 7}, 0.9)],
        ["STAFF"],
    )

    assert contexts[0].text == "前文。\n\n命中内容未结束\n\n继续说明未结束\n\n结束。"


@pytest.mark.asyncio
async def test_parent_text_is_cached_and_second_lookup_skips_mysql() -> None:
    repository = FakeSegmentRepository(
        [
            segment("parent", "完整章节内容", 1),
            segment("child", "章节片段", 2, parent_chunk_id="parent"),
        ]
    )
    text_cache = FakeTextCache()
    service = ContextExpansionService(repository, text_cache)  # type: ignore[arg-type]
    hits = [RetrievedEmbeddingSegment("child", "旧片段", {"docId": 7}, 0.9)]

    await service.expand(hits, ["STAFF"])

    assert text_cache.writes == [("parent", "完整章节内容")]
    assert repository.text_requests == [["parent"]]

    repository.text_requests.clear()
    contexts = await service.expand(hits, ["STAFF"])

    assert contexts[0].text == "完整章节内容"
    assert repository.text_requests == []


@pytest.mark.asyncio
async def test_cached_text_cannot_bypass_revoked_authorization() -> None:
    repository = FakeSegmentRepository([])
    text_cache = FakeTextCache({"parent": "已缓存正文", "child": "已缓存片段"})
    service = ContextExpansionService(repository, text_cache)  # type: ignore[arg-type]

    contexts = await service.expand(
        [RetrievedEmbeddingSegment("child", "旧片段", {"docId": 7}, 0.9)],
        ["STAFF"],
    )

    assert contexts == []
