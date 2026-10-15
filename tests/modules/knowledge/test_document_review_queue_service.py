"""知识文档审核队列测试：验证员工与领导的服务端可见范围。

定义 FakeDocumentRepository、主体构造函数和两项异步测试；测试确保客户端不能扩大
审核列表的上传人或状态范围。
"""

from datetime import datetime

import pytest

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.document_review_queue_service import (
    DocumentReviewQueueService,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    ListedKnowledgeDocument,
)


class FakeDocumentRepository:
    """记录审核队列查询参数的最小仓储替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    async def list_by_statuses(
        self, statuses: tuple[str, ...], *, upload_user: str | None = None, limit: int = 50
    ) -> list[ListedKnowledgeDocument]:
        assert limit == 50
        self.calls.append((statuses, upload_user))
        return [
            ListedKnowledgeDocument(
                document_id=7,
                title="维修手册.pdf",
                status=statuses[0],
                accessible_by=None,
                upload_user=upload_user,
                updated_at=datetime(2026, 8, 6),
            )
        ]


def staff_principal() -> SessionPrincipal:
    """构造普通员工可信主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id="STAFF-1", job="staff")


def leader_principal() -> SessionPrincipal:
    """构造领导可信主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id="LEADER-1", job="leader")


@pytest.mark.asyncio
async def test_staff_only_lists_own_pending_or_rejected_documents() -> None:
    repository = FakeDocumentRepository()
    service = DocumentReviewQueueService(repository)  # type: ignore[arg-type]

    await service.list_visible_documents(staff_principal())

    assert repository.calls == [(("PENDING_REVIEW", "REJECTED"), "STAFF-1")]


@pytest.mark.asyncio
async def test_leader_lists_all_pending_documents_only() -> None:
    repository = FakeDocumentRepository()
    service = DocumentReviewQueueService(repository)  # type: ignore[arg-type]

    await service.list_visible_documents(leader_principal())

    assert repository.calls == [(("PENDING_REVIEW",), None)]
