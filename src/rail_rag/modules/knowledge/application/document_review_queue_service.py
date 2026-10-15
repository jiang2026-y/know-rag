"""知识文档审核列表应用服务：按可信员工身份返回最小可见审核队列。

定义 DocumentReviewQueueService；普通员工只取得本人待审核或已驳回文档，
领导只取得全部待审核文档。服务不接受客户端提交的上传人或权限范围。
"""

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
    ListedKnowledgeDocument,
)


class DocumentReviewQueueService:
    """根据服务端可信主体收敛文档审核队列的可见范围。"""

    def __init__(self, repository: KnowledgeDocumentRepository) -> None:
        self._repository = repository

    async def list_visible_documents(
        self, principal: SessionPrincipal
    ) -> list[ListedKnowledgeDocument]:
        """返回当前员工可见的有限审核队列，顾客没有文档管理入口。"""
        if principal.principal_type != "STAFF":
            return []
        if principal.job == "leader":
            return await self._repository.list_by_statuses(("PENDING_REVIEW",))
        return await self._repository.list_by_statuses(
            ("PENDING_REVIEW", "REJECTED"), upload_user=principal.principal_id
        )
