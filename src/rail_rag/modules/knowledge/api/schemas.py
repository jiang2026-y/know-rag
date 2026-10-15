from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class StoredObject(BaseModel):
    bucket: str
    object_name: str
    url: str | None = None


class DocumentUploadResponse(BaseModel):
    original_filename: str
    source: StoredObject
    markdown: StoredObject
    images: list[StoredObject] = Field(default_factory=list)
    markdown_content: str = Field(description="MinerU-generated Markdown without image enrichment")


class MarkdownPreviewResponse(BaseModel):
    object_name: str
    content: str


class MarkdownDownloadResponse(BaseModel):
    object_name: str
    url: str
    expires_in_seconds: int


class DocumentTaskState(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"


class DocumentUploadTaskAccepted(BaseModel):
    task_id: str
    document_id: int
    state: DocumentTaskState
    document_status: str
    status_url: str


class DocumentUploadMetadata(BaseModel):
    document_id: int
    original_filename: str
    document_status: str
    source: StoredObject | None = None
    markdown: StoredObject | None = None
    images: list[StoredObject] = Field(default_factory=list)


class DocumentUploadTaskStatus(BaseModel):
    task_id: str
    document_id: int
    original_filename: str
    state: DocumentTaskState
    document_status: str
    created_at: datetime
    updated_at: datetime
    result: DocumentUploadMetadata | None = None
    error: str | None = None
    attempt_count: int


class DocumentReviewQueueItem(BaseModel):
    """审核队列中可安全展示的文档摘要。"""

    document_id: int
    title: str
    status: str
    accessible_by: str | None
    upload_user: str | None
    updated_at: datetime


class DocumentReviewQueueResponse(BaseModel):
    """当前可信员工可见的审核队列。"""

    documents: list[DocumentReviewQueueItem]
