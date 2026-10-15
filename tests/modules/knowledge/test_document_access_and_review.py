"""知识文档权限与审核测试：验证员工范围、待审核阅读和审核后向量化。

定义内存仓储与向量化替身，以及五项测试，覆盖 staff/leader 范围推导、待审核文档归属、
审核通过、驳回和非领导审核拒绝。
"""

from __future__ import annotations

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.document_read_access_service import (
    DocumentReadAccessService,
    KnowledgeDocumentNotReadableError,
)
from rail_rag.modules.knowledge.application.document_review_service import (
    DocumentReviewNotAllowedError,
    DocumentReviewService,
)
from rail_rag.modules.knowledge.domain.document_access import document_access_labels
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    StoredKnowledgeDocument,
)


class FakeDocumentRepository:
    """提供审核与阅读授权所需的最小内存文档仓储。"""

    def __init__(self, document: StoredKnowledgeDocument) -> None:
        self.document = document
        self.approved_with: list[str] = []
        self.rejected = False

    async def get(self, document_id: int) -> StoredKnowledgeDocument | None:
        return self.document if document_id == self.document.document_id else None

    async def approve_for_vectorization(
        self, document_id: int, accessible_by: str
    ) -> StoredKnowledgeDocument:
        assert document_id == self.document.document_id
        self.approved_with.append(accessible_by)
        self.document = StoredKnowledgeDocument(
            document_id=document_id,
            status="CHUNKED",
            version_id=self.document.version_id,
            accessible_by=accessible_by,  # type: ignore[arg-type]
            upload_user=self.document.upload_user,
            converted_url=self.document.converted_url,
        )
        return self.document

    async def reject_review(self, document_id: int) -> StoredKnowledgeDocument:
        assert document_id == self.document.document_id
        self.rejected = True
        self.document = StoredKnowledgeDocument(
            document_id=document_id,
            status="REJECTED",
            version_id=self.document.version_id,
            upload_user=self.document.upload_user,
            converted_url=self.document.converted_url,
        )
        return self.document


class FakeVectorizationService:
    """记录审核通过后触发的向量化调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    async def vectorize_document(self, document_id: int, version_id: int | None = None) -> bool:
        self.calls.append((document_id, version_id))
        return True


class FakeTaskRepository:
    """提供文档图片归属查询的空实现。"""

    async def get_image_object_names(self, document_id: int) -> list[str]:
        return []


def staff_principal(emp_id: str = "STAFF-1") -> SessionPrincipal:
    """创建用于测试的普通员工可信主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id=emp_id, job="staff")


def leader_principal() -> SessionPrincipal:
    """创建用于测试的领导可信主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id="LEADER-1", job="leader")


def test_leader_inherits_staff_document_scope() -> None:
    assert document_access_labels(staff_principal()) == ("VISITOR", "STAFF")
    assert document_access_labels(leader_principal()) == ("VISITOR", "STAFF", "LEADER")


@pytest.mark.asyncio
async def test_pending_document_is_only_readable_by_uploader_or_leader() -> None:
    document = StoredKnowledgeDocument(
        document_id=7,
        status="PENDING_REVIEW",
        version_id=9,
        upload_user="STAFF-1",
        converted_url="minio://knowledge/converted/doc.md",
    )
    service = DocumentReadAccessService(
        Settings(minio_bucket="knowledge"),
        FakeDocumentRepository(document),  # type: ignore[arg-type]
        FakeTaskRepository(),  # type: ignore[arg-type]
    )

    assert await service.markdown_object_name(staff_principal(), 7) == "converted/doc.md"
    assert await service.markdown_object_name(leader_principal(), 7) == "converted/doc.md"
    with pytest.raises(KnowledgeDocumentNotReadableError):
        await service.markdown_object_name(staff_principal("STAFF-2"), 7)


@pytest.mark.asyncio
async def test_leader_approval_sets_label_then_vectorizes() -> None:
    repository = FakeDocumentRepository(
        StoredKnowledgeDocument(7, "PENDING_REVIEW", version_id=9, upload_user="STAFF-1")
    )
    vectorization = FakeVectorizationService()
    service = DocumentReviewService(repository, vectorization)  # type: ignore[arg-type]

    result = await service.approve(leader_principal(), 7, "STAFF")

    assert result.status == "VECTOR_STORED"
    assert repository.approved_with == ["STAFF"]
    assert vectorization.calls == [(7, 9)]


@pytest.mark.asyncio
async def test_leader_rejection_does_not_vectorize() -> None:
    repository = FakeDocumentRepository(
        StoredKnowledgeDocument(7, "PENDING_REVIEW", version_id=9, upload_user="STAFF-1")
    )
    vectorization = FakeVectorizationService()
    service = DocumentReviewService(repository, vectorization)  # type: ignore[arg-type]

    result = await service.reject(leader_principal(), 7)

    assert result.status == "REJECTED"
    assert repository.rejected is True
    assert vectorization.calls == []


@pytest.mark.asyncio
async def test_staff_cannot_review_document() -> None:
    repository = FakeDocumentRepository(StoredKnowledgeDocument(7, "PENDING_REVIEW", version_id=9))
    service = DocumentReviewService(repository, FakeVectorizationService())  # type: ignore[arg-type]

    with pytest.raises(DocumentReviewNotAllowedError):
        await service.approve(staff_principal(), 7, "STAFF")
