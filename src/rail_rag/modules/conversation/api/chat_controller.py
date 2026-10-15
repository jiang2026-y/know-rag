"""对话会话接口：创建会话、保存首条消息并异步生成标题。"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from rail_rag.bootstrap import (
    get_chat_answer_service,
    get_chat_conversation_service,
    get_redis_distributed_lock,
)
from rail_rag.modules.identity.api.auth_controller import get_current_principal
from rail_rag.modules.identity.domain.principal import SessionPrincipal, conversation_owner_id
from rail_rag.modules.conversation.domain.conversation import (
    ConversationCreatedResponse,
    CreateConversationRequest,
    StreamChatMessageRequest,
)
from rail_rag.modules.conversation.application.conversation_service import ChatConversationService
from rail_rag.modules.conversation.application.chat_answer_service import (
    ChatAnswerService,
    ConversationNotFoundError,
)
from rail_rag.modules.conversation.domain.stream_event import ChatStreamEvent, encode_sse_event
from rail_rag.modules.knowledge.infrastructure.redis_distributed_lock import (
    RedisDistributedLock,
)

router = APIRouter(prefix="/chat", tags=["chat"])


class ConversationListItem(BaseModel):
    """会话列表中可展示的摘要。"""
    conversation_id: str
    title: str


class ConversationSnapshotResponse(BaseModel):
    """会话历史的安全响应。"""
    conversation_id: str
    title: str
    status: str
    messages: list[dict[str, object]]


@router.get("/conversations", response_model=list[ConversationListItem])
async def list_conversations(
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    service: Annotated[ChatConversationService, Depends(get_chat_conversation_service)],
) -> list[ConversationListItem]:
    """列出当前主体的会话，不接受客户端归属字段。"""
    rows = await service.list_conversations(conversation_owner_id(principal))
    return [ConversationListItem(conversation_id=row.conversation_id, title=row.title) for row in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationSnapshotResponse)
async def get_conversation(
    conversation_id: str,
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    service: Annotated[ChatConversationService, Depends(get_chat_conversation_service)],
) -> ConversationSnapshotResponse:
    """读取当前主体拥有的会话历史。"""
    snapshot = await service.get_snapshot(conversation_owner_id(principal), conversation_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Conversation was not found")
    return ConversationSnapshotResponse(
        conversation_id=snapshot.conversation_id,
        title=snapshot.title,
        status=snapshot.status,
        messages=[{"message_id": m.message_id, "role": m.role, "content": m.content, "citations": m.citations} for m in snapshot.messages],
    )

@router.post(
    "/conversations",
    response_model=ConversationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: CreateConversationRequest,
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    service: Annotated[ChatConversationService, Depends(get_chat_conversation_service)],
) -> ConversationCreatedResponse:
    """创建首轮会话；标题模型任务会在响应返回后继续执行。"""
    try:
        return await service.create_conversation(
            conversation_owner_id(principal), payload.first_message
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/conversations/{conversation_id}/messages:stream")
async def stream_chat_message(
    conversation_id: str,
    payload: StreamChatMessageRequest,
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    service: Annotated[ChatAnswerService, Depends(get_chat_answer_service)],
    lock: Annotated[RedisDistributedLock, Depends(get_redis_distributed_lock)],
) -> StreamingResponse:
    """按会话归属建立 SSE 回答流；同一主体同时只允许一条活动回答。"""
    lock_context = lock.acquire("chat-answer", conversation_owner_id(principal))
    await lock_context.__aenter__()
    trace_id = request.headers.get("X-Trace-Id") or str(uuid4())

    async def event_stream():
        sequence = 1
        try:
            yield encode_sse_event(
                ChatStreamEvent(
                    event="progress",
                    conversation_id=conversation_id,
                    message_id=payload.message_id or "",
                    sequence=sequence,
                    trace_id=trace_id,
                    payload={"stage": "QUERY_REWRITE", "message": "正在优化您的问题"},
                )
            )
            try:
                prepared = await service.prepare(
                    principal,
                    conversation_id,
                    message_id=payload.message_id,
                    message=payload.message,
                )
            except ConversationNotFoundError:
                sequence += 1
                yield encode_sse_event(
                    ChatStreamEvent(
                        event="error",
                        conversation_id=conversation_id,
                        message_id=payload.message_id or "",
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"code": "CHAT_CONVERSATION_NOT_FOUND", "message": "会话不可用，请重新开始对话。"},
                    )
                )
                sequence += 1
                yield encode_sse_event(
                    ChatStreamEvent(
                        event="done",
                        conversation_id=conversation_id,
                        message_id=payload.message_id or "",
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"status": "error", "answer_message_id": None},
                    )
                )
                return
            except ValueError:
                sequence += 1
                yield encode_sse_event(
                    ChatStreamEvent(
                        event="error",
                        conversation_id=conversation_id,
                        message_id=payload.message_id or "",
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"code": "CHAT_REQUEST_INVALID", "message": "问题格式无效，请重试。"},
                    )
                )
                sequence += 1
                yield encode_sse_event(
                    ChatStreamEvent(
                        event="done",
                        conversation_id=conversation_id,
                        message_id=payload.message_id or "",
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"status": "error", "answer_message_id": None},
                    )
                )
                return
            except Exception:
                sequence += 1
                yield encode_sse_event(
                    ChatStreamEvent(
                        event="error",
                        conversation_id=conversation_id,
                        message_id=payload.message_id or "",
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"code": "CHAT_PREPARE_UNAVAILABLE", "message": "暂时无法准备回答，请稍后重试。"},
                    )
                )
                sequence += 1
                yield encode_sse_event(
                    ChatStreamEvent(
                        event="done",
                        conversation_id=conversation_id,
                        message_id=payload.message_id or "",
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"status": "error", "answer_message_id": None},
                    )
                )
                return
            async for event in service.stream(prepared, trace_id, initial_sequence=sequence):
                if await request.is_disconnected():
                    return
                if event is None:
                    yield ": ping\n\n"
                else:
                    yield encode_sse_event(event)
                    sequence = event.sequence
        finally:
            await lock_context.__aexit__(None, None, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
