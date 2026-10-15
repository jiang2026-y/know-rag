"""文档处理任务状态机：定义调度端口、提交、执行、自动重试和死信重放准备。

定义 DocumentTaskDispatcher、DocumentTaskService 与任务异常；服务按版本上记录的切片参数
构造切片器，切片完成后失效父块正文缓存，并保证重试与死信重放的幂等。
"""

import logging
from collections.abc import Callable, Mapping
from typing import Protocol
from uuid import uuid4

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.api.schemas import (
    DocumentTaskState,
    DocumentUploadMetadata,
    DocumentUploadTaskAccepted,
    DocumentUploadTaskStatus,
    StoredObject,
)
from rail_rag.modules.knowledge.application.document_upload_service import DocumentUploadService
from rail_rag.modules.knowledge.application.document_vectorization_service import (
    DocumentVectorizationService,
)
from rail_rag.modules.knowledge.domain.document_split_param import (
    DocumentSplitParam,
    InvalidSplitParamError,
    parse_split_param,
)
from rail_rag.modules.knowledge.domain.document_splitter_factory import (
    DocumentSplitter,
)
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    MarkdownParentChildSplitter,
)
from rail_rag.modules.knowledge.infrastructure.document_task_repository import (
    DocumentTaskRepository,
    StoredDocumentTask,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    KnowledgeSegmentRepository,
)
from rail_rag.modules.knowledge.infrastructure.redis_distributed_lock import RedisDistributedLock


logger = logging.getLogger(__name__)


class DocumentTaskDispatcher(Protocol):
    """可靠任务调度端口，负责把已持久化的文档任务投递到独立 Worker。"""

    async def dispatch(self, task_id: str, delivery_attempt: int) -> str:
        """投递任务并返回 Relier/Celery 的任务标识。"""
        ...


class SegmentCacheInvalidator(Protocol):
    """定义按文档失效父块正文缓存的端口。"""

    async def invalidate_document(self, document_id: int) -> None:
        """使指定文档的分片正文缓存立即失效。"""


class DocumentTaskNotFoundError(LookupError):
    """任务 ID 不在持久化任务存储中时抛出。"""


class DocumentTaskRetryNotAllowedError(ValueError):
    """任务不满足人工重试前提时抛出。"""


class DocumentTaskService:
    """持久化文档任务状态机；执行、超时和 Worker 恢复由 Celery/Relier 管理。"""

    def __init__(
        self,
        settings: Settings,
        upload_service: DocumentUploadService,
        document_repository: KnowledgeDocumentRepository,
        task_repository: DocumentTaskRepository,
        segment_repository: KnowledgeSegmentRepository,
        vectorization_service: DocumentVectorizationService,
        lock: RedisDistributedLock,
        dispatcher: DocumentTaskDispatcher,
        splitter: MarkdownParentChildSplitter | None = None,
        splitter_factory: Callable[[DocumentSplitParam | None], DocumentSplitter] | None = None,
        segment_cache: SegmentCacheInvalidator | None = None,
    ) -> None:
        self._settings = settings
        self._upload_service = upload_service
        self._document_repository = document_repository
        self._task_repository = task_repository
        self._segment_repository = segment_repository
        self._vectorization_service = vectorization_service
        self._lock = lock
        self._dispatcher = dispatcher
        self._splitter = splitter or MarkdownParentChildSplitter()
        self._splitter_factory = splitter_factory
        self._segment_cache = segment_cache

    async def submit(
        self,
        filename: str,
        content: bytes,
        upload_user: str = "rail_rag",
        *,
        split_param: DocumentSplitParam | None = None,
    ) -> DocumentUploadTaskAccepted:
        """保存上传源文件和任务记录后，将任务投递到独立可靠队列。"""
        normalized_user = upload_user.strip()
        if not normalized_user:
            raise ValueError("upload_user must not be empty")
        async with self._lock.acquire(
            "document-upload", normalized_user, wait_time_seconds=0
        ):
            document_id = await self._document_repository.create_uploaded_document(
                filename,
                normalized_user,
                split_param=None if split_param is None else split_param.to_payload(),
            )
            version_id = await self._document_repository.get_current_version_id(
                document_id
            )
            task_id = str(uuid4())
            try:
                source = await self._upload_service.store_source_document(filename, content)
                await self._document_repository.update_source_url(
                    document_id, self._object_url(source.bucket, source.object_name)
                )
                task = await self._task_repository.create(
                    task_id, document_id, version_id, filename, source.object_name
                )
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                await self._document_repository.update_status(document_id, "FAILED")
                await self._task_repository.create(
                    task_id, document_id, version_id, filename, ""
                )
                await self._task_repository.fail(task_id, message)
                task = await self._task_repository.get(task_id)

        if task.state == "queued":
            await self._dispatch_or_defer(task.task_id, delivery_attempt=0)
        return self._accepted(task)

    async def get_status(self, task_id: str) -> DocumentUploadTaskStatus:
        """读取任务状态，不从 Celery 结果后端推导业务事实。"""
        return self._to_status(await self._get_task(task_id))

    async def retry(self, task_id: str) -> DocumentUploadTaskAccepted:
        """重投递保存源文件失败的普通失败任务；死信任务必须走 leader 重放接口。"""
        existing = await self._get_task(task_id)
        if existing.state != "failed":
            raise DocumentTaskRetryNotAllowedError(
                f"Only failed tasks can be retried; current state is {existing.state}"
            )
        if not existing.source_object_name:
            raise DocumentTaskRetryNotAllowedError(
                "Task source file was not stored; upload the document again"
            )
        task = await self._task_repository.requeue(task_id)
        if task is None:
            raise DocumentTaskRetryNotAllowedError(
                f"Only failed tasks can be retried; current state is {existing.state}"
            )
        await self._document_repository.update_status(task.document_id, "UPLOADED")
        await self._dispatch_or_defer(task.task_id, existing.attempt_count + 1)
        return self._accepted(task)

    async def cancel(self, task_id: str) -> DocumentUploadTaskAccepted:
        """取消排队或运行中的文档任务并返回最新状态。"""
        task = await self._task_repository.cancel(task_id)
        if task is None:
            raise DocumentTaskRetryNotAllowedError("Only queued or processing tasks can be cancelled")
        return self._accepted(task)

    async def redeliver_queued_tasks(self, limit: int = 100) -> int:
        """扫描并补发已持久化但尚未被 Worker 领取的任务，避免 Broker 短暂故障丢任务。"""
        queued_tasks = await self._task_repository.list_queued(limit)
        delivered = 0
        for task in queued_tasks:
            if await self._dispatch_or_defer(task.task_id, task.attempt_count):
                delivered += 1
        return delivered

    async def process_queued_task(self, task_id: str) -> dict[str, str]:
        """由 Relier Worker 调用一次；重复投递会因数据库状态机安全地忽略。"""
        task = await self._task_repository.start(task_id)
        if task is None:
            return {"status": "ignored"}
        if not task.source_object_name:
            raise RuntimeError("Task has no source object to process")

        await self._document_repository.update_status(task.document_id, "CONVERTING")
        result = await self._upload_service.convert_stored_document(
            task.original_filename, task.source_object_name
        )
        source_url = self._object_url(result.source.bucket, result.source.object_name)
        await self._split_document(
            task.document_id,
            task.version_id,
            result.markdown_content,
            task.original_filename,
            source_url,
        )
        await self._document_repository.complete_document(
            task.document_id,
            source_url,
            self._object_url(result.markdown.bucket, result.markdown.object_name),
        )
        await self._task_repository.complete(
            task_id,
            result.markdown.object_name,
            [image.object_name for image in result.images],
            "PENDING_REVIEW",
        )
        return {"status": "completed"}

    async def schedule_retry_or_dead_letter(self, task_id: str, error_message: str) -> bool:
        """在三次自动重试窗口内重新投递；耗尽后更新业务状态并让 Relier 写入 DLQ。"""
        task = await self._get_task(task_id)
        retried = await self._task_repository.requeue_after_failure(
            task_id, error_message, self._settings.task_max_auto_retries
        )
        if retried is not None:
            await self._document_repository.update_status(task.document_id, "UPLOADED")
            await self._dispatch_or_defer(task_id, delivery_attempt=task.attempt_count)
            return True

        dead_lettered = await self._task_repository.dead_letter(task_id, error_message)
        if dead_lettered is not None:
            await self._document_repository.update_status(task.document_id, "FAILED")
        return False

    async def prepare_dead_letter_replay(self, task_id: str) -> bool:
        """由 leader 重放前恢复 MySQL 状态并投递一个新的幂等交付轮次。"""
        task = await self._task_repository.replay_dead_lettered(task_id)
        if task is None:
            return False
        await self._document_repository.update_status(task.document_id, "UPLOADED")
        await self._dispatch_or_defer(task.task_id, task.attempt_count + 1)
        return True

    async def _dispatch_or_defer(self, task_id: str, delivery_attempt: int) -> bool:
        """投递失败时保留 queued 状态，由周期扫描器在任务 Redis 恢复后补发。"""
        try:
            await self._dispatcher.dispatch(task_id, delivery_attempt)
        except Exception as exc:
            logger.warning(
                "Document task dispatch deferred until the queue recovers.",
                extra={
                    "document_task_id": task_id,
                    "delivery_attempt": delivery_attempt,
                    "error_type": type(exc).__name__,
                },
            )
            return False
        return True

    async def _split_document(
        self,
        document_id: int,
        version_id: int,
        markdown: str,
        filename: str,
        source_url: str,
    ) -> None:
        async with self._lock.acquire(
            "document-split", document_id, wait_time_seconds=0
        ):
            splitter = await self._resolve_splitter(document_id, version_id)
            chunks = splitter.split(
                markdown,
                document_id=document_id,
                filename=filename,
                source_url=source_url,
            )
            await self._segment_repository.replace_for_document(
                document_id, version_id, chunks
            )
            if self._segment_cache is not None:
                # 重新切片后旧正文缓存立即失效，避免检索到上一版内容。
                await self._segment_cache.invalidate_document(document_id)

    async def _resolve_splitter(
        self, document_id: int, version_id: int
    ) -> DocumentSplitter:
        """按版本记录构造切片器；参数缺失或损坏时退回默认标题父子切分。"""
        if self._splitter_factory is None:
            return self._splitter
        reader = getattr(self._document_repository, "get_version_split_param", None)
        payload: Mapping[str, object] | None = None
        if reader is not None:
            try:
                stored = await reader(document_id, version_id)
            except Exception as exc:
                # 迁移未执行或该列不可读时按默认参数继续，不阻断文档处理。
                logger.warning(
                    "Unable to read stored split parameters; using defaults.",
                    extra={
                        "document_id": document_id,
                        "version_id": version_id,
                        "error_type": type(exc).__name__,
                    },
                )
                stored = None
            if isinstance(stored, Mapping):
                payload = stored
        try:
            return self._splitter_factory(parse_split_param(payload))
        except InvalidSplitParamError:
            logger.warning(
                "Stored split parameters are invalid; falling back to defaults.",
                extra={"document_id": document_id, "version_id": version_id},
            )
            return self._splitter_factory(None)

    async def _get_task(self, task_id: str) -> StoredDocumentTask:
        try:
            return await self._task_repository.get(task_id)
        except KeyError as exc:
            raise DocumentTaskNotFoundError(f"Document task not found: {task_id}") from exc

    def _accepted(self, task: StoredDocumentTask) -> DocumentUploadTaskAccepted:
        return DocumentUploadTaskAccepted(
            task_id=task.task_id,
            document_id=task.document_id,
            state=DocumentTaskState(task.state),
            document_status=task.document_status,
            status_url=f"/api/documents/tasks/{task.task_id}",
        )

    def _to_status(self, task: StoredDocumentTask) -> DocumentUploadTaskStatus:
        source = self._stored_object(task.source_object_name)
        markdown = self._stored_object(task.markdown_object_name)
        result = None
        if task.state == "completed":
            result = DocumentUploadMetadata(
                document_id=task.document_id,
                original_filename=task.original_filename,
                document_status=task.document_status,
                source=source,
                markdown=markdown,
                images=[
                    self._stored_object(object_name)
                    for object_name in task.image_object_names
                ],
            )

        return DocumentUploadTaskStatus(
            task_id=task.task_id,
            document_id=task.document_id,
            original_filename=task.original_filename,
            state=DocumentTaskState(task.state),
            document_status=task.document_status,
            created_at=task.created_at,
            updated_at=task.updated_at,
            result=result,
            error=task.error_message,
            attempt_count=task.attempt_count,
        )

    def _stored_object(self, object_name: str | None) -> StoredObject | None:
        if not object_name:
            return None
        return StoredObject(bucket=self._settings.minio_bucket, object_name=object_name)

    @staticmethod
    def _object_url(bucket: str, object_name: str) -> str:
        return f"minio://{bucket}/{object_name}"
