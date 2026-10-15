"""知识文档 HTTP 接口：上传、任务、审核、生命周期、切片参数与 Markdown/图片访问。

定义文档上传表单解析、审核与生命周期请求模型及各路由函数；上传接口按可选切片参数校验后
交给任务服务，生命周期变更与重新切片都会失效父块正文缓存。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, model_validator

from rail_rag.bootstrap import (
    get_document_read_access_service,
    get_document_review_service,
    get_document_review_queue_service,
    get_document_task_service,
    get_document_upload_service,
    get_markdown_access_service,
    get_knowledge_document_repository,
    get_redis_segment_text_cache,
)
from rail_rag.modules.identity.api.auth_controller import get_current_principal
from rail_rag.modules.admin.api.staff_access_controller import get_current_admin
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.api.schemas import (
    DocumentUploadTaskAccepted,
    DocumentUploadTaskStatus,
    DocumentReviewQueueItem,
    DocumentReviewQueueResponse,
    MarkdownDownloadResponse,
    MarkdownPreviewResponse,
)
from rail_rag.modules.knowledge.application.document_upload_service import DocumentUploadService
from rail_rag.modules.knowledge.domain.document_split_param import (
    InvalidSplitParamError,
    parse_split_param,
)
from rail_rag.modules.knowledge.application.document_task_service import (
    DocumentTaskNotFoundError,
    DocumentTaskRetryNotAllowedError,
    DocumentTaskService,
)
from rail_rag.modules.knowledge.application.markdown_access_service import MarkdownAccessService
from rail_rag.modules.knowledge.application.document_read_access_service import (
    DocumentReadAccessService,
    KnowledgeDocumentNotFoundError,
    KnowledgeDocumentNotReadableError,
)
from rail_rag.modules.knowledge.application.document_review_service import (
    DocumentReviewNotAllowedError,
    DocumentReviewService,
    DocumentReviewStateError,
)
from rail_rag.modules.knowledge.application.document_review_queue_service import (
    DocumentReviewQueueService,
)

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentReviewRequest(BaseModel):
    """领导对已解析文档作出的通过或驳回决定。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    accessible_by: Literal["VISITOR", "STAFF", "LEADER"] | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "DocumentReviewRequest":
        """审核通过必须指定范围，驳回不得附带范围。"""
        if self.decision == "APPROVE" and self.accessible_by is None:
            raise ValueError("Approved documents require an access label")
        if self.decision == "REJECT" and self.accessible_by is not None:
            raise ValueError("Rejected documents must not include an access label")
        return self


class DocumentReviewResponse(BaseModel):
    """领导审核操作完成后的文档发布状态。"""

    document_id: int
    status: str
    accessible_by: Literal["VISITOR", "STAFF", "LEADER"] | None


class DocumentLifecycleRequest(BaseModel):
    """领导更新文档生命周期状态。"""
    model_config = ConfigDict(extra="forbid")
    lifecycle_status: Literal["ENABLED", "DISABLED", "ARCHIVED", "DELETED"]


@router.patch("/{document_id}/lifecycle")
async def update_document_lifecycle(
    document_id: int,
    payload: DocumentLifecycleRequest,
    principal: Annotated[SessionPrincipal, Depends(get_current_admin)],
    repository=Depends(get_knowledge_document_repository),
    segment_cache=Depends(get_redis_segment_text_cache),
) -> dict[str, object]:
    """由 leader 控制文档启用、停用、归档或软删除状态，并失效父块正文缓存。"""
    await repository.update_lifecycle_status(document_id, payload.lifecycle_status)
    await segment_cache.invalidate_document(document_id)
    return {"document_id": document_id, "lifecycle_status": payload.lifecycle_status}

@router.post(
    "/upload",
    response_model=DocumentUploadTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(  #接口函数，读取并校验文件
    file: Annotated[UploadFile, File(description="PDF, DOC, or DOCX document to convert")],
    upload_service: Annotated[
        DocumentUploadService, Depends(get_document_upload_service)
    ],
    task_service: Annotated[DocumentTaskService, Depends(get_document_task_service)],
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    split_type: Annotated[
        str | None,
        Form(description="切片策略：TITLE、LENGTH、SMART、REGEX 或 SEPARATOR"),
    ] = None,
    chunk_size: Annotated[int | None, Form(description="每片最大字符数，默认 1000")] = None,
    overlap: Annotated[int | None, Form(description="相邻分片重叠字符数，默认 100")] = None,
    title_level: Annotated[int | None, Form(description="参与切分的标题层级 1-6，默认 6")] = None,
    separator: Annotated[str | None, Form(description="SEPARATOR 策略使用的分隔符")] = None,
    regex: Annotated[str | None, Form(description="REGEX 策略使用的正则表达式")] = None,
) -> DocumentUploadTaskAccepted:
    if principal.principal_type != "STAFF":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Employee access is required")
    try:
        split_param = parse_split_param(
            {
                "split_type": split_type,
                "chunk_size": chunk_size,
                "overlap": overlap,
                "title_level": title_level,
                "separator": separator,
                "regex": regex,
            }
        )
    except InvalidSplitParamError as exc:
        # 使用状态码常量在部分 Starlette 版本已被标记弃用，这里固定返回 422。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename, content = await upload_service.read_and_validate_upload(file)
    return await task_service.submit(
        filename, content, principal.principal_id, split_param=split_param
    )


@router.get("/tasks/{task_id}", response_model=DocumentUploadTaskStatus)
async def get_document_task_status(
    task_id: str,
    task_service: Annotated[DocumentTaskService, Depends(get_document_task_service)],
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    access_service: Annotated[
        DocumentReadAccessService, Depends(get_document_read_access_service)
    ],
) -> DocumentUploadTaskStatus:
    try:
        task = await task_service.get_status(task_id)
        await access_service.readable_document(principal, task.document_id)
        return task
    except DocumentTaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeDocumentNotReadableError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post(
    "/tasks/{task_id}/retry",
    response_model=DocumentUploadTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document_task(
    task_id: str,
    task_service: Annotated[DocumentTaskService, Depends(get_document_task_service)],
    principal: Annotated[SessionPrincipal, Depends(get_current_admin)],
    access_service: Annotated[
        DocumentReadAccessService, Depends(get_document_read_access_service)
    ],
) -> DocumentUploadTaskAccepted:
    try:
        task = await task_service.get_status(task_id)
        await access_service.readable_document(principal, task.document_id)
        return await task_service.retry(task_id)
    except DocumentTaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentTaskRetryNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeDocumentNotReadableError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/cancel", response_model=DocumentUploadTaskAccepted)
async def cancel_document_task(
    task_id: str,
    task_service: Annotated[DocumentTaskService, Depends(get_document_task_service)],
    principal: Annotated[SessionPrincipal, Depends(get_current_admin)],
) -> DocumentUploadTaskAccepted:
    """由有权限员工取消尚未完成的文档任务。"""
    try:
        return await task_service.cancel(task_id)
    except DocumentTaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentTaskRetryNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/review-queue", response_model=DocumentReviewQueueResponse)
async def get_review_queue(
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    service: Annotated[
        DocumentReviewQueueService, Depends(get_document_review_queue_service)
    ],
) -> DocumentReviewQueueResponse:
    """返回当前员工可见的审核队列，范围仅由服务端 Session 推导。"""
    documents = await service.list_visible_documents(principal)
    return DocumentReviewQueueResponse(
        documents=[DocumentReviewQueueItem(**document.__dict__) for document in documents]
    )


@router.post("/{document_id}/review", response_model=DocumentReviewResponse)
async def review_document(
    document_id: int,
    payload: DocumentReviewRequest,
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    service: Annotated[DocumentReviewService, Depends(get_document_review_service)],
) -> DocumentReviewResponse:
    """由 leader 审核已解析文档；只有通过动作会触发向量化。"""
    try:
        if payload.decision == "APPROVE":
            result = await service.approve(principal, document_id, payload.accessible_by)
        else:
            result = await service.reject(principal, document_id)
    except DocumentReviewNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except DocumentReviewStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DocumentReviewResponse(**result.__dict__)


@router.get("/markdown/preview", response_model=MarkdownPreviewResponse)
async def preview_markdown(
    document_id: int = Query(description="Knowledge document ID"),
    principal: SessionPrincipal = Depends(get_current_principal),
    access_service: DocumentReadAccessService = Depends(get_document_read_access_service),
    service: MarkdownAccessService = Depends(get_markdown_access_service),
) -> MarkdownPreviewResponse:
    try:
        object_name = await access_service.markdown_object_name(principal, document_id)
        return await service.preview(object_name)
    except KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeDocumentNotReadableError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/markdown/download", response_model=MarkdownDownloadResponse)
async def download_markdown(
    document_id: int = Query(description="Knowledge document ID"),
    principal: SessionPrincipal = Depends(get_current_principal),
    access_service: DocumentReadAccessService = Depends(get_document_read_access_service),
    service: MarkdownAccessService = Depends(get_markdown_access_service),
) -> MarkdownDownloadResponse:
    try:
        object_name = await access_service.markdown_object_name(principal, document_id)
        return await service.create_download_url(object_name)
    except KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeDocumentNotReadableError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/images/download", response_model=MarkdownDownloadResponse)
async def download_image(
    document_id: int = Query(description="Knowledge document ID"),
    object_name: str = Query(
        description="A generated images/<group>/<file> object name"
    ),
    principal: SessionPrincipal = Depends(get_current_principal),
    access_service: DocumentReadAccessService = Depends(get_document_read_access_service),
    service: MarkdownAccessService = Depends(get_markdown_access_service),
) -> MarkdownDownloadResponse:
    try:
        permitted_object_name = await access_service.image_object_name(
            principal, document_id, object_name
        )
        return await service.create_image_download_url(permitted_object_name)
    except KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeDocumentNotReadableError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
