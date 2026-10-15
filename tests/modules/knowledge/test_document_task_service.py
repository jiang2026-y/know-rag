import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.api.schemas import DocumentTaskState, DocumentUploadResponse, StoredObject
from rail_rag.modules.knowledge.infrastructure.document_task_repository import StoredDocumentTask
from rail_rag.modules.knowledge.application.document_task_service import (
    DocumentTaskNotFoundError,
    DocumentTaskRetryNotAllowedError,
    DocumentTaskService,
)
from rail_rag.modules.knowledge.domain.document_split_param import (
    DocumentSplitParam,
    parse_split_param,
)
from rail_rag.modules.knowledge.domain.document_splitter_factory import (
    build_document_splitter,
)
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import MarkdownChunk


class ControlledUploadService:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def store_source_document(self, filename: str, content: bytes) -> StoredObject:
        assert filename == "sample.pdf"
        assert content == b"%PDF-1.7 test"
        return StoredObject(bucket="test", object_name="uploads/source.pdf")

    async def convert_stored_document(
        self, filename: str, source_object_name: str
    ) -> DocumentUploadResponse:
        assert filename == "sample.pdf"
        assert source_object_name == "uploads/source.pdf"
        self.started.set()
        await self.release.wait()
        return DocumentUploadResponse(
            original_filename=filename,
            source=StoredObject(bucket="test", object_name="uploads/source.pdf"),
            markdown=StoredObject(bucket="test", object_name="converted/result.md"),
            images=[StoredObject(bucket="test", object_name="images/group/page.png")],
            markdown_content="# Parsed",
        )


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.statuses: list[tuple[int, str]] = []
        self.source_urls: list[tuple[int, str]] = []
        self.completed: list[tuple[int, str, str]] = []
        self.split_params: list[dict[str, object] | None] = []
        self.stored_split_param: dict[str, object] | None = None

    async def create_uploaded_document(
        self,
        title: str,
        upload_user: str,
        *,
        split_param: dict[str, object] | None = None,
    ) -> int:
        assert title == "sample.pdf"
        assert upload_user == "rail_rag"
        self.split_params.append(split_param)
        if split_param is not None:
            self.stored_split_param = split_param
        return 42

    async def get_version_split_param(
        self, document_id: int, version_id: int
    ) -> dict[str, object] | None:
        assert document_id == 42
        assert version_id == 100
        return self.stored_split_param

    async def get_current_version_id(self, document_id: int) -> int:
        assert document_id == 42
        return 100

    async def update_status(self, document_id: int, status: str) -> None:
        self.statuses.append((document_id, status))

    async def update_source_url(self, document_id: int, source_url: str) -> None:
        self.source_urls.append((document_id, source_url))

    async def complete_document(
        self, document_id: int, source_url: str, converted_url: str
    ) -> None:
        self.completed.append((document_id, source_url, converted_url))


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, StoredDocumentTask] = {}

    async def create(
        self,
        task_id: str,
        document_id: int,
        version_id: int,
        filename: str,
        source_object_name: str,
    ) -> StoredDocumentTask:
        now = datetime.now(UTC)
        task = StoredDocumentTask(
            task_id=task_id,
            document_id=document_id,
            version_id=version_id,
            original_filename=filename,
            state="queued",
            document_status="uploaded",
            source_object_name=source_object_name,
            markdown_object_name=None,
            image_object_names=[],
            error_message=None,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        self.tasks[task_id] = task
        return task

    async def get(self, task_id: str) -> StoredDocumentTask:
        if task_id not in self.tasks:
            raise KeyError(task_id)
        return self.tasks[task_id]

    async def list_queued(self, limit: int = 100) -> list[StoredDocumentTask]:
        return [task for task in self.tasks.values() if task.state == "queued"][:limit]

    async def start(self, task_id: str) -> StoredDocumentTask | None:
        task = await self.get(task_id)
        if task.state != "queued":
            return None
        task = replace(
            task,
            state="processing",
            document_status="converting",
            attempt_count=task.attempt_count + 1,
            updated_at=datetime.now(UTC),
        )
        self.tasks[task_id] = task
        return task

    async def complete(
        self,
        task_id: str,
        markdown_object_name: str,
        image_object_names: list[str],
        document_status: str = "vector_stored",
    ) -> None:
        task = await self.get(task_id)
        self.tasks[task_id] = replace(
            task,
            state="completed",
            document_status=document_status,
            markdown_object_name=markdown_object_name,
            image_object_names=image_object_names,
            updated_at=datetime.now(UTC),
        )

    async def fail(self, task_id: str, error_message: str) -> None:
        task = await self.get(task_id)
        self.tasks[task_id] = replace(
            task,
            state="failed",
            document_status="failed",
            error_message=error_message,
            updated_at=datetime.now(UTC),
        )

    async def requeue(self, task_id: str) -> StoredDocumentTask | None:
        task = await self.get(task_id)
        if task.state != "failed":
            return None
        task = replace(
            task,
            state="queued",
            document_status="uploaded",
            error_message=None,
            updated_at=datetime.now(UTC),
        )
        self.tasks[task_id] = task
        return task

    async def requeue_after_failure(
        self, task_id: str, error_message: str, max_auto_retries: int
    ) -> StoredDocumentTask | None:
        task = await self.get(task_id)
        if task.state != "processing" or task.attempt_count > max_auto_retries:
            return None
        task = replace(
            task,
            state="queued",
            document_status="UPLOADED",
            error_message=error_message,
            updated_at=datetime.now(UTC),
        )
        self.tasks[task_id] = task
        return task

    async def dead_letter(self, task_id: str, error_message: str) -> StoredDocumentTask | None:
        task = await self.get(task_id)
        if task.state != "processing":
            return None
        task = replace(
            task,
            state="dead_lettered",
            document_status="FAILED",
            error_message=error_message,
            updated_at=datetime.now(UTC),
        )
        self.tasks[task_id] = task
        return task

    async def replay_dead_lettered(self, task_id: str) -> StoredDocumentTask | None:
        task = await self.get(task_id)
        if task.state != "dead_lettered":
            return None
        task = replace(
            task,
            state="queued",
            document_status="UPLOADED",
            error_message=None,
            updated_at=datetime.now(UTC),
        )
        self.tasks[task_id] = task
        return task

    async def mark_interrupted_tasks_failed(self) -> list[int]:
        return []


class FakeSegmentRepository:
    def __init__(self) -> None:
        self.saved: list[tuple[int, list[MarkdownChunk]]] = []
        self.vectorized: list[tuple[int, list[str]]] = []

    async def replace_for_document(
        self, document_id: int, version_id: int, chunks: list[MarkdownChunk]
    ) -> None:
        assert version_id == 100
        self.saved.append((document_id, chunks))

    async def mark_vectors_stored(self, document_id: int, chunk_ids: list[str]) -> None:
        self.vectorized.append((document_id, chunk_ids))


class FakeSegmentCache:
    """记录按文档失效父块正文缓存的调用。"""

    def __init__(self) -> None:
        self.invalidated: list[int] = []

    async def invalidate_document(self, document_id: int) -> None:
        self.invalidated.append(document_id)


class FakeVectorizationService:
    def __init__(self) -> None:
        self.vectorized_document_ids: list[int] = []

    async def vectorize_document(self, document_id: int, version_id: int | None = None) -> bool:
        assert version_id == 100
        self.vectorized_document_ids.append(document_id)
        return True


class FakeDistributedLock:
    def __init__(self) -> None:
        self.acquisitions: list[tuple[str, str | int, float]] = []

    @asynccontextmanager
    async def acquire(
        self, scene: str, key: str | int, *, wait_time_seconds: float = 0
    ):
        self.acquisitions.append((scene, key, wait_time_seconds))
        yield


class FakeDocumentTaskDispatcher:
    def __init__(self) -> None:
        self.deliveries: list[tuple[str, int]] = []

    async def dispatch(self, task_id: str, delivery_attempt: int) -> str:
        self.deliveries.append((task_id, delivery_attempt))
        return f"relier-{len(self.deliveries)}"


def make_service() -> tuple[
    DocumentTaskService,
    ControlledUploadService,
    FakeDocumentRepository,
    FakeSegmentRepository,
    FakeVectorizationService,
    FakeDistributedLock,
]:
    upload_service = ControlledUploadService()
    document_repository = FakeDocumentRepository()
    segment_repository = FakeSegmentRepository()
    vectorization_service = FakeVectorizationService()
    lock = FakeDistributedLock()
    dispatcher = FakeDocumentTaskDispatcher()
    service = DocumentTaskService(
        Settings(),
        upload_service,
        document_repository,
        FakeTaskRepository(),
        segment_repository,
        vectorization_service,
        lock,
        dispatcher,
    )
    return (
        service,
        upload_service,
        document_repository,
        segment_repository,
        vectorization_service,
        lock,
    )


@pytest.mark.asyncio
async def test_submit_persists_metadata_before_conversion_completes() -> None:
    (
        service,
        upload_service,
        document_repository,
        segment_repository,
        vectorization_service,
        lock,
    ) = make_service()

    accepted = await service.submit("sample.pdf", b"%PDF-1.7 test")

    assert accepted.state == DocumentTaskState.QUEUED
    assert accepted.document_id == 42
    assert document_repository.source_urls == [
        (42, "minio://test/uploads/source.pdf")
    ]
    assert service._dispatcher.deliveries == [(accepted.task_id, 0)]
    worker = asyncio.create_task(service.process_queued_task(accepted.task_id))
    await upload_service.started.wait()
    assert (await service.get_status(accepted.task_id)).state == DocumentTaskState.PROCESSING

    upload_service.release.set()
    await worker
    result = await service.get_status(accepted.task_id)

    assert result.state == DocumentTaskState.COMPLETED
    assert result.document_status == "PENDING_REVIEW"
    assert result.attempt_count == 1
    assert result.result is not None
    assert result.result.markdown.object_name == "converted/result.md"
    assert [image.object_name for image in result.result.images] == ["images/group/page.png"]
    assert document_repository.statuses == [(42, "CONVERTING")]
    assert len(segment_repository.saved) == 1
    saved_document_id, chunks = segment_repository.saved[0]
    assert saved_document_id == 42
    assert chunks[0].metadata["docId"] == 42
    assert vectorization_service.vectorized_document_ids == []
    assert lock.acquisitions == [
        ("document-upload", "rail_rag", 0),
        ("document-split", 42, 0),
    ]
    assert document_repository.completed == [
        (
            42,
            "minio://test/uploads/source.pdf",
            "minio://test/converted/result.md",
        )
    ]


@pytest.mark.asyncio
async def test_failed_task_can_be_retried() -> None:
    service, upload_service, document_repository, _, _, _ = make_service()
    accepted = await service.submit("sample.pdf", b"%PDF-1.7 test")
    worker = asyncio.create_task(service.process_queued_task(accepted.task_id))
    await upload_service.started.wait()
    upload_service.release.set()
    await worker

    task_repository = service._task_repository
    await task_repository.fail(accepted.task_id, "MinerU unavailable")

    retried = await service.retry(accepted.task_id)

    assert retried.state == DocumentTaskState.QUEUED
    assert retried.document_id == 42
    assert document_repository.statuses[-1] == (42, "UPLOADED")


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_task() -> None:
    service, upload_service, _, _, _, _ = make_service()
    accepted = await service.submit("sample.pdf", b"%PDF-1.7 test")

    with pytest.raises(DocumentTaskRetryNotAllowedError):
        await service.retry(accepted.task_id)



@pytest.mark.asyncio
async def test_unknown_task_raises_not_found() -> None:
    service, _, _, _, _, _ = make_service()

    with pytest.raises(DocumentTaskNotFoundError):
        await service.get_status("missing-task")


@pytest.mark.asyncio
async def test_failed_worker_attempts_are_requeued_then_dead_lettered() -> None:
    service, _, document_repository, _, _, _ = make_service()
    accepted = await service.submit("sample.pdf", b"%PDF-1.7 test")
    task_repository = service._task_repository

    assert await task_repository.start(accepted.task_id) is not None
    assert await service.schedule_retry_or_dead_letter(accepted.task_id, "temporary failure")
    assert (await service.get_status(accepted.task_id)).state == DocumentTaskState.QUEUED
    assert service._dispatcher.deliveries[-1] == (accepted.task_id, 1)

    for attempt in (2, 3):
        assert await task_repository.start(accepted.task_id) is not None
        assert await service.schedule_retry_or_dead_letter(accepted.task_id, "temporary failure")
        assert service._dispatcher.deliveries[-1] == (accepted.task_id, attempt)

    assert await task_repository.start(accepted.task_id) is not None
    assert not await service.schedule_retry_or_dead_letter(accepted.task_id, "permanent failure")
    assert (await service.get_status(accepted.task_id)).state == DocumentTaskState.DEAD_LETTERED
    assert document_repository.statuses[-1] == (42, "FAILED")


@pytest.mark.asyncio
async def test_queued_tasks_can_be_redelivered_after_broker_recovery() -> None:
    service, _, _, _, _, _ = make_service()
    accepted = await service.submit("sample.pdf", b"%PDF-1.7 test")

    delivered = await service.redeliver_queued_tasks()

    assert delivered == 1
    assert service._dispatcher.deliveries == [(accepted.task_id, 0), (accepted.task_id, 0)]


@pytest.mark.asyncio
async def test_submit_persists_validated_split_parameters() -> None:
    service, _, document_repository, _, _, _ = make_service()
    param = parse_split_param({"split_type": "smart", "chunk_size": "800"})

    await service.submit("sample.pdf", b"%PDF-1.7 test", "rail_rag", split_param=param)

    assert document_repository.split_params == [
        {
            "split_type": "SMART",
            "chunk_size": 800,
            "overlap": 80,
            "title_level": 6,
            "separator": None,
            "regex": None,
        }
    ]


@pytest.mark.asyncio
async def test_worker_uses_stored_split_parameters_and_invalidates_cache() -> None:
    upload_service = ControlledUploadService()
    document_repository = FakeDocumentRepository()
    document_repository.stored_split_param = {
        "split_type": "LENGTH",
        "chunk_size": 12,
        "overlap": 0,
        "title_level": 6,
        "separator": None,
        "regex": None,
    }
    segment_repository = FakeSegmentRepository()
    segment_cache = FakeSegmentCache()
    used: list[DocumentSplitParam | None] = []

    def factory(param: DocumentSplitParam | None):
        used.append(param)
        return build_document_splitter(param)

    service = DocumentTaskService(
        Settings(),
        upload_service,
        document_repository,
        FakeTaskRepository(),
        segment_repository,
        FakeVectorizationService(),
        FakeDistributedLock(),
        FakeDocumentTaskDispatcher(),
        splitter_factory=factory,
        segment_cache=segment_cache,
    )
    accepted = await service.submit("sample.pdf", b"%PDF-1.7 test")
    worker = asyncio.create_task(service.process_queued_task(accepted.task_id))
    await upload_service.started.wait()
    upload_service.release.set()
    await worker

    assert used and used[0] is not None
    assert used[0].split_type == "LENGTH"
    assert used[0].chunk_size == 12
    assert segment_cache.invalidated == [42]
    _, chunks = segment_repository.saved[0]
    assert [chunk.text for chunk in chunks] == ["# Parsed"]
    assert chunks[0].skip_embedding is False
    assert "parentChunkId" not in chunks[0].metadata
