"""统一证据重排服务：安全调用 qwen3-rerank，并记录保留与淘汰原因。

定义 RerankOutcome 和 RerankService；服务仅重排已授权的知识库、图数据库或当前车主关系库证据，
模型故障或未配置时按初始融合顺序降级，不会扩大任何数据范围。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalResult,
)
from rail_rag.modules.qa.infrastructure.dashscope_rerank_client import (
    RerankClient,
    RerankClientError,
)


@dataclass(frozen=True)
class RerankOutcome:
    """重排后的最终证据与可持久化审计信息。"""

    evidence: list[KnowledgeRetrievalResult]
    status: Literal["APPLIED", "NOT_CONFIGURED", "FALLBACK", "SKIPPED"]
    audit: dict[str, Any]


class RerankService:
    """以固定文本截断和稳定次序执行重排，避免模型错误影响问答主链路。"""

    def __init__(
        self,
        client: RerankClient,
        *,
        enabled: bool,
        top_k: int,
        document_max_characters: int,
    ) -> None:
        self._client = client
        self._enabled = enabled
        self._top_k = top_k
        self._document_max_characters = document_max_characters

    async def rerank(
        self, query: str, evidence: list[KnowledgeRetrievalResult]
    ) -> RerankOutcome:
        """返回前 N 条重排结果；失败时稳定降级到初始召回顺序。"""
        if not evidence:
            return RerankOutcome([], "SKIPPED", self._audit(evidence, {}, set(), "no_candidates"))
        initial = evidence[: self._top_k]
        if not self._enabled:
            kept = {item.chunk_id for item in initial}
            return RerankOutcome(
                initial,
                "NOT_CONFIGURED",
                self._audit(evidence, {}, kept, "rerank_not_configured"),
            )
        documents = [item.text[: self._document_max_characters] for item in evidence]
        try:
            scores = await self._client.rerank(query, documents, top_n=min(self._top_k, len(documents)))
        except RerankClientError:
            kept = {item.chunk_id for item in initial}
            return RerankOutcome(
                initial,
                "FALLBACK",
                self._audit(evidence, {}, kept, "rerank_request_failed"),
            )
        except Exception:
            # 适配器外的异常同样不得阻断回答；安全回退到已授权的初始候选。
            kept = {item.chunk_id for item in initial}
            return RerankOutcome(
                initial,
                "FALLBACK",
                self._audit(evidence, {}, kept, "rerank_request_failed"),
            )
        ranked = sorted(scores, key=lambda item: (-item.relevance_score, item.index))
        selected = ranked[: self._top_k]
        score_by_index = {item.index: item.relevance_score for item in ranked}
        reranked = [
            replace(evidence[item.index], rerank_score=item.relevance_score)
            for item in selected
            if item.index < len(evidence)
        ]
        kept = {item.chunk_id for item in reranked}
        return RerankOutcome(
            reranked,
            "APPLIED",
            self._audit(evidence, score_by_index, kept, "lower_rerank_score"),
        )

    @staticmethod
    def _audit(
        evidence: list[KnowledgeRetrievalResult],
        score_by_index: dict[int, float],
        kept_ids: set[str],
        drop_reason: str,
    ) -> dict[str, Any]:
        """构造不含原文的最小检索和重排审计数据。"""
        return {
            "candidate_count": len(evidence),
            "evidence": [
                {
                    "chunk_id": item.chunk_id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "raw_score": item.score,
                    "raw_scores": item.raw_scores,
                    "retrieval_methods": list(item.retrieval_methods),
                    "rrf_score": item.rrf_score,
                    "rerank_score": score_by_index.get(index),
                    "retained": item.chunk_id in kept_ids,
                    "drop_reason": None if item.chunk_id in kept_ids else drop_reason,
                }
                for index, item in enumerate(evidence)
            ],
        }
