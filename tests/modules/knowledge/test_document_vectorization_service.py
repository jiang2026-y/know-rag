"""验证按文档状态和每页 100 条切片执行的向量化流程。"""

from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from rail_rag.modules.knowledge.application.document_vectorization_service import (
    DocumentVectorizationService,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    StoredKnowledgeDocument,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    StoredKnowledgeSegment,
)


@dataclass
class FakeDocumentRepository:
    document: StoredKnowledgeDocument | None
    statuses: list[tuple[int, str]]

    async def get(self, document_id: int) -> StoredKnowledgeDocument | None:
        if self.document is not None and self.document.document_id == document_id:
            return self.document
        return None

    async def update_status(self, document_id: int, status: str) -> None:
        self.statuses.append((document_id, status))


class FakeSegmentRepository:
    def __init__(self, segments: list[StoredKnowledgeSegment]) -> None:
        self.pending = segments
        self.page_limits: list[int] = []
        self.stored: list[list[tuple[int, str]]] = []

    async def list_pending_for_embedding(
        self, document_id: int, document_version: int, *, limit: int = 100
    ) -> list[StoredKnowledgeSegment]:
        assert document_id == 7
        assert document_version == 9
        self.page_limits.append(limit)
        return self.pending[:limit]

    async def mark_vectors_stored(self, embeddings: list[tuple[int, str]]) -> None:
        self.stored.append(embeddings)
        ids = {segment_id for segment_id, _ in embeddings}
        self.pending = [segment for segment in self.pending if segment.id not in ids]


class FakeEmbeddingStore:
    def __init__(self) -> None:
        self.reset_ids: list[int] = []
        self.page_sizes: list[int] = []

    async def reset_document(self, document_id: int) -> None:
        self.reset_ids.append(document_id)

    async def store_segments(
        self, segments: list[StoredKnowledgeSegment]
    ) -> list[tuple[int, str]]:
        self.page_sizes.append(len(segments))
        return [(segment.id, segment.chunk_id) for segment in segments]


class FakeDistributedLock:
    def __init__(self) -> None:
        self.acquisitions: list[tuple[str, str | int, float]] = []

    @asynccontextmanager
    async def acquire(
        self, scene: str, key: str | int, *, wait_time_seconds: float = 0
    ):
        self.acquisitions.append((scene, key, wait_time_seconds))
        yield


@pytest.mark.asyncio
async def test_vectorizes_pending_segments_in_pages_of_one_hundred() -> None:
    segments = [
        StoredKnowledgeSegment(index, f"chunk-{index}", "text", {"docId": 7})
        for index in range(101)
    ]
    documents = FakeDocumentRepository(
        StoredKnowledgeDocument(7, "converted", 9), []
    )
    repository = FakeSegmentRepository(segments)
    store = FakeEmbeddingStore()
    lock = FakeDistributedLock()
    service = DocumentVectorizationService(documents, repository, store, lock)

    assert await service.vectorize_document(7) is True
    assert store.reset_ids == [7]
    assert store.page_sizes == [100, 1]
    assert repository.page_limits == [100, 100, 100]
    assert documents.statuses == [(7, "VECTOR_STORED")]
    assert lock.acquisitions == [("document-split", 7, 0)]


@pytest.mark.asyncio
async def test_returns_false_for_missing_document_and_skips_completed_document() -> None:
    store = FakeEmbeddingStore()
    lock = FakeDistributedLock()
    missing = DocumentVectorizationService(
        FakeDocumentRepository(None, []), FakeSegmentRepository([]), store, lock
    )
    assert await missing.vectorize_document(7) is False

    completed = DocumentVectorizationService(
        FakeDocumentRepository(StoredKnowledgeDocument(7, "VECTOR_STORED", 9), []),
        FakeSegmentRepository([]),
        store,
        lock,
    )
    assert await completed.vectorize_document(7) is True
    assert store.reset_ids == []
