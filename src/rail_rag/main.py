from fastapi import FastAPI, Request
from uuid import uuid4
from fastapi.responses import JSONResponse

from rail_rag.modules.identity.api.auth_controller import router as auth_router
from rail_rag.modules.identity.api.internal_staff_provision_controller import (
    router as internal_hr_router,
)
from rail_rag.modules.identity.infrastructure.auth_repository import AuthenticationError
from rail_rag.modules.identity.infrastructure.session_service import (
    InvalidSessionError,
    SessionBackendError,
)
from rail_rag.modules.conversation.api.chat_controller import (
    router as chat_router,
)
from rail_rag.modules.conversation.api.feedback_controller import router as feedback_router
from rail_rag.modules.conversation.infrastructure.feedback_repository import FeedbackRepositoryError
from rail_rag.modules.conversation.infrastructure.conversation_repository import (
    ChatConversationRepositoryError,
)
from rail_rag.modules.knowledge.api.document_controller import router as document_router
from rail_rag.modules.admin.api.task_dead_letter_controller import (
    router as task_dead_letter_router,
)
from rail_rag.modules.knowledge.api.knowledge_retrieval_controller import (
    router as knowledge_retrieval_router,
)
from rail_rag.modules.admin.api.staff_access_controller import router as admin_router
from rail_rag.modules.identity.infrastructure.staff_access_repository import (
    StaffAccessRepositoryError,
)
from rail_rag.modules.identity.infrastructure.staff_password_provision_repository import (
    StaffPasswordProvisionRepositoryError,
)
from rail_rag.modules.knowledge.infrastructure.mineru_client import MinerUError
from rail_rag.modules.knowledge.application.document_upload_service import InvalidDocumentError
from rail_rag.modules.knowledge.application.markdown_access_service import InvalidMarkdownObjectError
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepositoryError,
)
from rail_rag.modules.knowledge.infrastructure.document_task_repository import DocumentTaskRepositoryError
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    KnowledgeSegmentRepositoryError,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    ElasticsearchEmbeddingStoreError,
)
from rail_rag.modules.knowledge.infrastructure.redis_distributed_lock import (
    DistributedLockBackendError,
    DistributedLockNotAcquiredError,
)

app = FastAPI(title="LLMcopy", version="0.1.0")


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """为每个 HTTP 请求补充可关联日志与 SSE 的 trace_id。"""
    trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response
for _api_prefix in ("/api", "/api/v1"):
    app.include_router(auth_router, prefix=_api_prefix)
    app.include_router(document_router, prefix=_api_prefix)
    app.include_router(knowledge_retrieval_router, prefix=_api_prefix)
    app.include_router(chat_router, prefix=_api_prefix)
    app.include_router(feedback_router, prefix=_api_prefix)
    app.include_router(admin_router, prefix=_api_prefix)
    app.include_router(task_dead_letter_router, prefix=_api_prefix)
app.include_router(internal_hr_router)


@app.exception_handler(InvalidDocumentError)
async def invalid_document_handler(_: Request, exc: InvalidDocumentError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(MinerUError)
async def mineru_error_handler(_: Request, exc: MinerUError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(InvalidMarkdownObjectError)
async def invalid_markdown_object_handler(
    _: Request, exc: InvalidMarkdownObjectError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(KnowledgeDocumentRepositoryError)
async def knowledge_document_repository_error_handler(
    _: Request, exc: KnowledgeDocumentRepositoryError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(DocumentTaskRepositoryError)
async def document_task_repository_error_handler(
    _: Request, exc: DocumentTaskRepositoryError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(KnowledgeSegmentRepositoryError)
async def knowledge_segment_repository_error_handler(
    _: Request, exc: KnowledgeSegmentRepositoryError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ElasticsearchEmbeddingStoreError)
async def elasticsearch_embedding_store_error_handler(
    _: Request, exc: ElasticsearchEmbeddingStoreError
) -> JSONResponse:
    """将知识检索的 Elasticsearch 不可用状态转换为服务不可用响应。"""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(DistributedLockNotAcquiredError)
async def distributed_lock_not_acquired_handler(
    _: Request, exc: DistributedLockNotAcquiredError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(DistributedLockBackendError)
async def distributed_lock_backend_handler(
    _: Request, exc: DistributedLockBackendError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ChatConversationRepositoryError)
async def chat_conversation_repository_error_handler(
    _: Request, exc: ChatConversationRepositoryError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(FeedbackRepositoryError)
async def feedback_repository_error_handler(_: Request, exc: FeedbackRepositoryError) -> JSONResponse:
    """将反馈存储故障转换为不泄露内部实现的服务不可用响应。"""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(StaffAccessRepositoryError)
async def staff_access_repository_error_handler(
    _: Request, exc: StaffAccessRepositoryError
) -> JSONResponse:
    """将员工访问、幂等和审计存储故障转换为安全的服务不可用响应。"""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(_: Request, exc: AuthenticationError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(StaffPasswordProvisionRepositoryError)
async def staff_password_provision_error_handler(
    _: Request, exc: StaffPasswordProvisionRepositoryError
) -> JSONResponse:
    """将 HR 内部开户存储故障转换为服务不可用，避免泄露数据库细节。"""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(SessionBackendError)
async def session_backend_error_handler(_: Request, exc: SessionBackendError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(InvalidSessionError)
async def invalid_session_error_handler(_: Request, exc: InvalidSessionError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc)},
        headers={"WWW-Authenticate": "Session"},
    )
