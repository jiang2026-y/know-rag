"""统一证据重排服务测试：验证排序、审计字段和上游失败降级。"""

import pytest

from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalResult,
)
from rail_rag.modules.qa.application.rerank_service import RerankService
from rail_rag.modules.qa.infrastructure.dashscope_rerank_client import (
    RerankClientError,
    RerankScore,
)


class FakeRerankClient:
    """返回固定分数或模拟重排服务不可用。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def rerank(self, query: str, documents: list[str], *, top_n: int) -> list[RerankScore]:
        if self.fail:
            raise RerankClientError("upstream unavailable")
        assert query == "车辆保养周期"
        assert documents == ["第一条", "第二条", "第三条"]
        assert top_n == 2
        return [RerankScore(index=2, relevance_score=0.95), RerankScore(index=0, relevance_score=0.7)]


def candidates() -> list[KnowledgeRetrievalResult]:
    """构造带 RRF 原始审计信息的三个授权候选。"""
    return [
        KnowledgeRetrievalResult("one", "第一条", 0.03, 1, raw_scores={"vector": 0.8}, rrf_score=0.03),
        KnowledgeRetrievalResult("two", "第二条", 0.02, 2, raw_scores={"full_text": 4.0}, rrf_score=0.02),
        KnowledgeRetrievalResult("three", "第三条", 0.01, 3, raw_scores={"vector": 0.6}, rrf_score=0.01),
    ]


@pytest.mark.asyncio
async def test_reranks_and_records_discard_reason() -> None:
    service = RerankService(FakeRerankClient(), enabled=True, top_k=2, document_max_characters=4000)

    outcome = await service.rerank("车辆保养周期", candidates())

    assert outcome.status == "APPLIED"
    assert [item.chunk_id for item in outcome.evidence] == ["three", "one"]
    assert outcome.evidence[0].rerank_score == 0.95
    assert outcome.audit["evidence"][1]["drop_reason"] == "lower_rerank_score"


@pytest.mark.asyncio
async def test_falls_back_to_initial_order_when_rerank_fails() -> None:
    service = RerankService(FakeRerankClient(fail=True), enabled=True, top_k=2, document_max_characters=4000)

    outcome = await service.rerank("车辆保养周期", candidates())

    assert outcome.status == "FALLBACK"
    assert [item.chunk_id for item in outcome.evidence] == ["one", "two"]
    assert outcome.audit["evidence"][2]["drop_reason"] == "rerank_request_failed"
