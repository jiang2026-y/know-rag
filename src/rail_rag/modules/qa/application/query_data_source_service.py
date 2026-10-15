"""受控数据源检索编排：并行召回多路数据、统一 RRF 融合并回落知识库。

定义 DataSourceRetrievalOutcome 和 QueryDataSourceService；服务端固定三类适配器，
禁止把模型输出、用户 ID 或 Cypher/SQL 直接转发给数据源，并在结构化源无证据或故障时
回落一次知识库检索并记录审计原因。
"""

import asyncio
from dataclasses import dataclass, replace
import logging

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalResult,
    KnowledgeRetrievalService,
)
from rail_rag.modules.qa.domain.query_route import QueryRouteStrategy
from rail_rag.modules.qa.infrastructure.neo4j_graph_retriever import Neo4jGraphRetriever
from rail_rag.modules.vehicle.infrastructure.my_car_record_retriever import (
    MyCarRecordRetriever,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataSourceRetrievalOutcome:
    """多路召回证据及其结构化源回落知识库的审计信息。"""

    evidence: list[KnowledgeRetrievalResult]
    fallbacks: list[dict[str, str]]


class QueryDataSourceService:
    """按白名单策略检索单一数据源，并统一转换为回答可用证据。"""

    _KNOWLEDGE_BASE: QueryRouteStrategy = "knowledge_base"
    _FALLBACK_METHOD_PREFIX = "fallback_from:"

    def __init__(
        self,
        knowledge_retrieval: KnowledgeRetrievalService,
        graph_retriever: Neo4jGraphRetriever,
        vehicle_retriever: MyCarRecordRetriever,
        settings: Settings | None = None,
    ) -> None:
        self._knowledge_retrieval = knowledge_retrieval
        self._graph_retriever = graph_retriever
        self._vehicle_retriever = vehicle_retriever
        self._rrf_k = (settings or Settings()).chat_rag_rrf_k

    async def retrieve(
        self,
        principal: SessionPrincipal,
        strategy: QueryRouteStrategy,
        question: str,
        *,
        limit: int,
    ) -> list[KnowledgeRetrievalResult]:
        """仅执行与策略匹配的受控检索；单一数据源故障或无结果不自动越权降级。"""
        if strategy == "knowledge_base":
            return await self._knowledge_base(principal, question, limit=limit)
        # 适配器故障在此上抛，由多路编排统一判定是否回落知识库，避免静默返回空证据。
        return await self._retrieve_structured(
            principal, strategy, question, limit=limit
        )

    async def retrieve_many(
        self,
        principal: SessionPrincipal,
        strategies: list[QueryRouteStrategy] | tuple[QueryRouteStrategy, ...],
        question: str,
        *,
        limit: int,
    ) -> list[KnowledgeRetrievalResult]:
        """并行召回已授权数据源，并按跨数据源排名执行统一 RRF。"""
        outcome = await self.retrieve_many_with_audit(
            principal, strategies, question, limit=limit
        )
        return outcome.evidence

    async def retrieve_many_with_audit(
        self,
        principal: SessionPrincipal,
        strategies: list[QueryRouteStrategy] | tuple[QueryRouteStrategy, ...],
        question: str,
        *,
        limit: int,
    ) -> DataSourceRetrievalOutcome:
        """并行召回已授权数据源；结构化源无证据时回落一次知识库检索并返回审计信息。"""
        unique_strategies = list(dict.fromkeys(strategies))
        if not unique_strategies:
            return DataSourceRetrievalOutcome([], [])
        settled = await asyncio.gather(
            *(
                self.retrieve(principal, strategy, question, limit=limit)
                for strategy in unique_strategies
            ),
            return_exceptions=True,
        )
        results_by_strategy: list[list[KnowledgeRetrievalResult]] = []
        fallbacks: list[dict[str, str]] = []
        structured_without_evidence: list[QueryRouteStrategy] = []
        for strategy, result in zip(unique_strategies, settled):
            if isinstance(result, Exception):
                # 单一路径故障不扩大权限，也不阻断其他已授权路径。
                fallbacks.append({"from": strategy, "reason": "retriever_error"})
                if strategy != self._KNOWLEDGE_BASE:
                    structured_without_evidence.append(strategy)
                continue
            results_by_strategy.append(result)
            if strategy != self._KNOWLEDGE_BASE and not result:
                structured_without_evidence.append(strategy)
        for strategy in structured_without_evidence:
            if not any(entry["from"] == strategy for entry in fallbacks):
                fallbacks.append({"from": strategy, "reason": "empty_result"})
        if structured_without_evidence and self._KNOWLEDGE_BASE not in unique_strategies:
            # 路由没有要求知识库且结构化源全部无证据时补一路知识库检索，
            # 证据上标记 fallback_from:<strategy>，避免前端误以为证据来自结构化数据源。
            knowledge_base_results = await self._knowledge_base_or_empty(
                principal, question, limit=limit
            )
            if knowledge_base_results:
                results_by_strategy.append(
                    self._mark_fallback(
                        knowledge_base_results, source=structured_without_evidence[0]
                    )
                )
        return DataSourceRetrievalOutcome(
            self._rrf_merge(results_by_strategy), fallbacks
        )

    async def _knowledge_base(
        self, principal: SessionPrincipal, question: str, *, limit: int
    ) -> list[KnowledgeRetrievalResult]:
        """执行仅含当前主体授权范围的知识库混合召回。"""
        return await self._knowledge_retrieval.search(principal, question, limit=limit)

    async def _knowledge_base_or_empty(
        self, principal: SessionPrincipal, question: str, *, limit: int
    ) -> list[KnowledgeRetrievalResult]:
        """回落检索失败时返回空证据，不影响其他已授权路径的回答。"""
        try:
            # 走公共入口，保证测试替身和未来的装饰实现都能接管知识库检索。
            return await self.retrieve(
                principal, self._KNOWLEDGE_BASE, question, limit=limit
            )
        except Exception as exc:
            logger.warning(
                "Knowledge base fallback retrieval failed.",
                extra={"error_type": type(exc).__name__},
            )
            return []

    async def _retrieve_structured(
        self,
        principal: SessionPrincipal,
        strategy: QueryRouteStrategy,
        question: str,
        *,
        limit: int,
    ) -> list[KnowledgeRetrievalResult]:
        """执行图数据库或当前车主关系库受控检索。"""
        if strategy == "graph_db":
            records = await self._graph_retriever.search(question, limit=limit)
            return [
                KnowledgeRetrievalResult(
                    chunk_id=f"graph:{record.source_id}",
                    text=record.text,
                    score=1.0,
                    document_id=None,
                    source_type="graph_db",
                    source_id=record.source_id,
                )
                for record in records
            ]
        records = await self._vehicle_retriever.search(principal, question, limit=limit)
        return [
            KnowledgeRetrievalResult(
                chunk_id=f"my_car:{record.source_id}",
                text=record.text,
                score=1.0,
                document_id=None,
                source_type="relational_db",
                source_id="my_car",
            )
            for record in records
        ]

    def _mark_fallback(
        self, results: list[KnowledgeRetrievalResult], *, source: str
    ) -> list[KnowledgeRetrievalResult]:
        """标记本轮证据来自结构化数据源空结果后的知识库回落。"""
        marker = f"{self._FALLBACK_METHOD_PREFIX}{source}"
        return [
            replace(item, retrieval_methods=(*item.retrieval_methods, marker))
            for item in results
        ]

    def _rrf_merge(
        self, results_by_strategy: list[list[KnowledgeRetrievalResult]]
    ) -> list[KnowledgeRetrievalResult]:
        """按每一路的名次合并证据，重复 chunk 会累加 RRF 分数。"""
        merged: dict[str, tuple[KnowledgeRetrievalResult, float]] = {}
        for results in results_by_strategy:
            for rank, item in enumerate(results, start=1):
                key = f"{item.source_type}:{item.chunk_id}"
                contribution = 1.0 / (self._rrf_k + rank)
                if key in merged:
                    existing, score = merged[key]
                    merged[key] = (existing, score + contribution)
                else:
                    merged[key] = (item, contribution)
        fused = [
            replace(item, score=rrf_score, rrf_score=rrf_score)
            for item, rrf_score in (value for value in merged.values())
        ]
        source_priority = {"relational_db": 0, "graph_db": 1, "knowledge_base": 2}
        return sorted(
            fused,
            key=lambda item: (
                -item.score,
                source_priority.get(item.source_type, 3),
                item.chunk_id,
            ),
        )
