"""知识检索应用服务：从可信主体推导范围并执行 Elasticsearch 向量检索。

定义 KnowledgeRetrievalService 和 KnowledgeRetrievalResult；服务负责在调用检索适配器前
计算服务端可见标签，避免客户端提交或扩大权限范围。
"""

from dataclasses import dataclass, field
from typing import Literal

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.domain.document_access import document_access_labels
from rail_rag.modules.knowledge.application.context_expansion_service import (
    ContextExpansionService,
    ExpandedKnowledgeContext,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    ElasticsearchEmbeddingStore,
)


@dataclass(frozen=True)
class KnowledgeRetrievalResult:
    """受权限过滤后的一个知识分片命中。"""

    chunk_id: str
    text: str
    score: float
    document_id: int | None
    source_type: Literal["knowledge_base", "graph_db", "relational_db"] = "knowledge_base"
    source_id: str | None = None
    retrieval_methods: tuple[str, ...] = ()
    raw_scores: dict[str, float] = field(default_factory=dict)
    rrf_score: float | None = None
    rerank_score: float | None = None
    document_title: str = ""
    section: str = ""


class KnowledgeRetrievalService:
    """执行仅包含当前可信主体可见文档的向量检索。"""

    def __init__(
        self,
        embedding_store: ElasticsearchEmbeddingStore,
        context_expansion_service: ContextExpansionService,
        settings: Settings | None = None,
    ) -> None:
        self._embedding_store = embedding_store
        self._context_expansion_service = context_expansion_service
        runtime = settings or Settings()
        self._vector_limit = runtime.chat_rag_vector_top_k
        self._full_text_limit = runtime.chat_rag_full_text_top_k
        self._rrf_k = runtime.chat_rag_rrf_k

    async def search(
        self, principal: SessionPrincipal, question: str, *, limit: int
    ) -> list[KnowledgeRetrievalResult]:
        """根据当前 Session 权限过滤 ES 命中并转换为 API 安全结果。"""
        accessible_by = list(document_access_labels(principal))
        hybrid_search = getattr(self._embedding_store, "search_hybrid_segments", None)
        if hybrid_search is None:
            results = await self._embedding_store.search_segments(
                question,
                accessible_by=accessible_by,
                limit=limit,
            )
        else:
            results = await hybrid_search(
                question,
                accessible_by=accessible_by,
                vector_limit=self._vector_limit,
                full_text_limit=self._full_text_limit,
                rrf_k=self._rrf_k,
            )
        contexts = await self._context_expansion_service.expand(results, accessible_by)
        return [self._result_from_context(context) for context in contexts[:limit]]

    @staticmethod
    def _result_from_context(context: ExpandedKnowledgeContext) -> KnowledgeRetrievalResult:
        """将二次授权和父子块补全结果转换为问答安全上下文。"""
        return KnowledgeRetrievalResult(
            chunk_id=context.chunk_id,
            text=context.text,
            score=context.score,
            document_id=context.document_id,
            retrieval_methods=context.retrieval_methods,
            raw_scores=context.raw_scores,
            rrf_score=context.rrf_score,
            document_title=context.document_title,
            section=context.section,
        )
