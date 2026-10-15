"""知识检索应用服务测试：验证 Elasticsearch 查询范围只能由可信主体推导。

定义记录调用参数的向量存储替身及两项测试，分别验证普通员工和领导的检索标签。
"""

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalService,
)
from rail_rag.modules.knowledge.application.context_expansion_service import (
    ExpandedKnowledgeContext,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    RetrievedEmbeddingSegment,
)


class FakeEmbeddingStore:
    """记录应用服务提交的 ES 可见范围。"""

    def __init__(self) -> None:
        self.accessible_by: list[str] | None = None

    async def search_segments(
        self, question: str, *, accessible_by: list[str], limit: int
    ) -> list[RetrievedEmbeddingSegment]:
        assert question == "如何交付车辆"
        assert limit == 5
        self.accessible_by = accessible_by
        return [
            RetrievedEmbeddingSegment(
                chunk_id="chunk-1",
                text="交付检查流程",
                metadata={"docId": 12},
                score=0.9,
            )
        ]


class FakeContextExpansionService:
    """记录二次授权范围，并返回已补全的安全上下文。"""

    def __init__(self) -> None:
        self.accessible_by: list[str] | None = None

    async def expand(self, hits, accessible_by: list[str]):
        self.accessible_by = accessible_by
        return [ExpandedKnowledgeContext("chunk-1", "完整章节", 0.9, 12)]


async def test_staff_search_uses_staff_scope() -> None:
    store = FakeEmbeddingStore()
    expansion = FakeContextExpansionService()
    service = KnowledgeRetrievalService(store, expansion)  # type: ignore[arg-type]

    hits = await service.search(
        SessionPrincipal(principal_type="STAFF", principal_id="EMP-1", job="staff"),
        "如何交付车辆",
        limit=5,
    )

    assert store.accessible_by == ["VISITOR", "STAFF"]
    assert expansion.accessible_by == ["VISITOR", "STAFF"]
    assert hits[0].document_id == 12


async def test_leader_search_inherits_staff_scope() -> None:
    store = FakeEmbeddingStore()
    expansion = FakeContextExpansionService()
    service = KnowledgeRetrievalService(store, expansion)  # type: ignore[arg-type]

    await service.search(
        SessionPrincipal(principal_type="STAFF", principal_id="EMP-1", job="leader"),
        "如何交付车辆",
        limit=5,
    )

    assert store.accessible_by == ["VISITOR", "STAFF", "LEADER"]
    assert expansion.accessible_by == ["VISITOR", "STAFF", "LEADER"]
