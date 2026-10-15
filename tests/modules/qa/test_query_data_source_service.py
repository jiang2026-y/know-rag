"""多路数据源检索测试：验证统一 RRF 融合、结构化事实优先级与知识库回落审计。

定义可控的多数据源替身与五项测试，覆盖跨源 RRF 排序、结构化源空结果或故障时
回落一次知识库检索、已请求知识库时不重复查询，以及审计原因分类。
"""

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalResult,
)
from rail_rag.modules.qa.application.query_data_source_service import QueryDataSourceService


class FakeMultiSourceService(QueryDataSourceService):
    """使用固定结果替代三个真实数据源，并可模拟单路故障与调用记录。"""

    def __init__(
        self,
        results: dict[str, list[KnowledgeRetrievalResult]],
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._results = results
        self._errors = errors or {}
        self.calls: list[str] = []
        self._rrf_k = 60

    async def retrieve(self, principal, strategy, question, *, limit):
        """返回指定路由的固定证据。"""
        self.calls.append(strategy)
        if strategy in self._errors:
            raise self._errors[strategy]
        return self._results.get(strategy, [])[:limit]


def customer() -> SessionPrincipal:
    """创建用于检索权限参数传递的顾客主体。"""
    return SessionPrincipal(principal_type="CUSTOMER", principal_id="customer-1")


@pytest.mark.asyncio
async def test_retrieve_many_fuses_sources_and_prefers_structured_ties() -> None:
    service = FakeMultiSourceService(
        {
            "relational_db": [
                KnowledgeRetrievalResult(
                    "my_car:car-1", "保险到期日：2026-12-31", 1.0, None, "relational_db", "my_car"
                )
            ],
            "knowledge_base": [
                KnowledgeRetrievalResult("chunk-1", "保险到期说明", 0.8, 1),
                KnowledgeRetrievalResult("chunk-2", "续保注意事项", 0.7, 1),
            ],
        }
    )

    result = await service.retrieve_many(
        customer(),
        ["relational_db", "knowledge_base"],
        "我的保险什么时候到期？",
        limit=5,
    )

    assert [item.source_type for item in result] == [
        "relational_db",
        "knowledge_base",
        "knowledge_base",
    ]
    assert result[0].rrf_score == pytest.approx(1 / 61)
    assert result[0].score == result[0].rrf_score


@pytest.mark.asyncio
async def test_empty_structured_sources_fall_back_to_knowledge_base() -> None:
    service = FakeMultiSourceService(
        {
            "graph_db": [],
            "knowledge_base": [
                KnowledgeRetrievalResult("chunk-1", "纯电车型说明", 0.8, 3)
            ],
        }
    )

    outcome = await service.retrieve_many_with_audit(
        customer(), ["graph_db"], "纯电车型都有哪些？", limit=5
    )

    assert service.calls == ["graph_db", "knowledge_base"]
    assert [item.chunk_id for item in outcome.evidence] == ["chunk-1"]
    assert outcome.evidence[0].retrieval_methods == ("fallback_from:graph_db",)
    assert outcome.fallbacks == [{"from": "graph_db", "reason": "empty_result"}]


@pytest.mark.asyncio
async def test_retriever_error_falls_back_and_is_audited_as_error() -> None:
    service = FakeMultiSourceService(
        {
            "knowledge_base": [
                KnowledgeRetrievalResult("chunk-9", "保险到期说明", 0.7, 4)
            ]
        },
        errors={"relational_db": RuntimeError("mysql is unavailable")},
    )

    outcome = await service.retrieve_many_with_audit(
        customer(), ["relational_db"], "我的保险还有多久到期？", limit=5
    )

    assert service.calls == ["relational_db", "knowledge_base"]
    assert outcome.evidence[0].retrieval_methods == ("fallback_from:relational_db",)
    assert outcome.fallbacks == [{"from": "relational_db", "reason": "retriever_error"}]


@pytest.mark.asyncio
async def test_requested_knowledge_base_is_not_queried_twice() -> None:
    service = FakeMultiSourceService(
        {
            "relational_db": [],
            "knowledge_base": [
                KnowledgeRetrievalResult("chunk-3", "保养周期说明", 0.6, 5)
            ],
        }
    )

    outcome = await service.retrieve_many_with_audit(
        customer(), ["relational_db", "knowledge_base"], "保养周期是多久？", limit=5
    )

    assert service.calls == ["relational_db", "knowledge_base"]
    assert [item.chunk_id for item in outcome.evidence] == ["chunk-3"]
    assert outcome.evidence[0].retrieval_methods == ()
    assert outcome.fallbacks == [{"from": "relational_db", "reason": "empty_result"}]


@pytest.mark.asyncio
async def test_structured_evidence_skips_fallback_and_audit() -> None:
    service = FakeMultiSourceService(
        {
            "relational_db": [
                KnowledgeRetrievalResult(
                    "my_car:car-1", "保险到期日：2026-12-31", 1.0, None, "relational_db", "my_car"
                )
            ]
        }
    )

    outcome = await service.retrieve_many_with_audit(
        customer(), ["relational_db"], "我的保险什么时候到期？", limit=5
    )

    assert service.calls == ["relational_db"]
    assert outcome.fallbacks == []
    assert outcome.evidence[0].retrieval_methods == ()
