"""知识文档阅读授权服务：校验文档状态、上传归属和可见标签后返回 Markdown 对象名。

定义 DocumentReadAccessService、KnowledgeDocumentNotReadableError 与
KnowledgeDocumentNotFoundError；服务负责将待审核、驳回和已发布文档的阅读规则统一在服务端执行。
"""

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.domain.document_access import (
    can_view_pending_document,
    document_access_labels,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
    StoredKnowledgeDocument,
)
from rail_rag.modules.knowledge.infrastructure.document_task_repository import (
    DocumentTaskRepository,
)


class KnowledgeDocumentNotFoundError(LookupError):
    """请求的知识文档不存在时抛出。"""


class KnowledgeDocumentNotReadableError(PermissionError):
    """当前主体无权读取指定知识文档时抛出。"""


class DocumentReadAccessService:
    """按审核状态和文档标签校验 Markdown 预览、下载权限。"""

    def __init__(
        self,
        settings: Settings,
        repository: KnowledgeDocumentRepository,
        task_repository: DocumentTaskRepository,
    ) -> None:
        self._bucket = settings.minio_bucket
        self._repository = repository
        self._task_repository = task_repository

    async def markdown_object_name(
        self, principal: SessionPrincipal, document_id: int
    ) -> str:
        """验证主体可读后，从受控转换结果 URL 提取 Markdown 对象名。"""
        document = await self.readable_document(principal, document_id)
        if not document.converted_url:
            raise KnowledgeDocumentNotReadableError("Knowledge document has no converted Markdown")
        prefix = f"minio://{self._bucket}/"
        if not document.converted_url.startswith(prefix):
            raise KnowledgeDocumentNotReadableError("Knowledge document storage location is invalid")
        return document.converted_url.removeprefix(prefix)

    async def readable_document(
        self, principal: SessionPrincipal, document_id: int
    ) -> StoredKnowledgeDocument:
        """返回已完成当前主体阅读授权检查的文档元数据。"""
        document = await self._repository.get(document_id)
        if document is None:
            raise KnowledgeDocumentNotFoundError("Knowledge document was not found")
        self._ensure_readable(principal, document)
        return document

    async def image_object_name(
        self, principal: SessionPrincipal, document_id: int, object_name: str
    ) -> str:
        """校验主体与图片归属后返回可供 MinIO 下载的图片对象名。"""
        await self.readable_document(principal, document_id)
        image_object_names = await self._task_repository.get_image_object_names(document_id)
        if object_name not in image_object_names:
            raise KnowledgeDocumentNotReadableError("Image does not belong to this document")
        return object_name

    def _ensure_readable(
        self, principal: SessionPrincipal, document: StoredKnowledgeDocument
    ) -> None:
        if document.lifecycle_status != "ENABLED":
            raise KnowledgeDocumentNotReadableError("Document is not available for this user")
        if document.status != "VECTOR_STORED":
            if can_view_pending_document(principal, document.upload_user):
                return
            raise KnowledgeDocumentNotReadableError("Document is not available for this user")
        if document.accessible_by not in document_access_labels(principal):
            raise KnowledgeDocumentNotReadableError("Document is not available for this user")
