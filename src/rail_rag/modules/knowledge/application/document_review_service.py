"""知识文档审核应用服务：编排领导审核、权限标签写入及审核后向量化。

定义 DocumentReviewService、DocumentReviewNotAllowedError 与 DocumentReviewStateError；
服务负责校验审核者、提交通过/驳回决定，并在通过后调用既有向量化服务。
"""

from dataclasses import dataclass

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.document_vectorization_service import (
    DocumentVectorizationService,
)
from rail_rag.modules.knowledge.domain.document_access import (
    DocumentAccessLabel,
    can_review_documents,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
    KnowledgeDocumentReviewStateError,
)


class DocumentReviewNotAllowedError(PermissionError):
    """非领导员工尝试审核知识文档时抛出。"""


class DocumentReviewStateError(ValueError):
    """审核动作与当前文档状态不兼容时抛出。"""


@dataclass(frozen=True)
class DocumentReviewResult:
    """一次审核决定完成后返回的文档状态与可见标签。"""

    document_id: int
    status: str
    accessible_by: DocumentAccessLabel | None


class DocumentReviewService:
    """仅允许 leader 对已解析文档决定发布范围或驳回。"""

    def __init__(
        self,
        repository: KnowledgeDocumentRepository,
        vectorization_service: DocumentVectorizationService,
    ) -> None:
        self._repository = repository
        self._vectorization_service = vectorization_service

    async def approve(
        self,
        reviewer: SessionPrincipal,
        document_id: int,
        accessible_by: DocumentAccessLabel,
    ) -> DocumentReviewResult:
        """写入审核通过标签，并在标签已落库后同步向量索引。"""
        self._ensure_reviewer(reviewer)
        try:
            document = await self._repository.approve_for_vectorization(
                document_id, accessible_by
            )
        except KnowledgeDocumentReviewStateError as exc:
            raise DocumentReviewStateError(str(exc)) from exc
        if document.version_id is None:
            raise DocumentReviewStateError("Knowledge document has no active version")
        await self._vectorization_service.vectorize_document(
            document.document_id, document.version_id
        )
        return DocumentReviewResult(
            document_id=document.document_id,
            status="VECTOR_STORED",
            accessible_by=accessible_by,
        )

    async def reject(
        self, reviewer: SessionPrincipal, document_id: int
    ) -> DocumentReviewResult:
        """驳回待审核文档，且不触发向量化或检索发布。"""
        self._ensure_reviewer(reviewer)
        try:
            document = await self._repository.reject_review(document_id)
        except KnowledgeDocumentReviewStateError as exc:
            raise DocumentReviewStateError(str(exc)) from exc
        return DocumentReviewResult(document.document_id, document.status, None)

    @staticmethod
    def _ensure_reviewer(reviewer: SessionPrincipal) -> None:
        if not can_review_documents(reviewer):
            raise DocumentReviewNotAllowedError("Leader access is required for document review")
