"""回答反馈接口：接收当前用户对助手回答的赞或踩。

定义 SubmitFeedbackRequest、SubmitFeedbackResponse 与 submit_feedback；接口从可信
Session 获取评价主体，要求幂等键且不接受客户端提供的会话、引用或权限信息。
"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from rail_rag.bootstrap import get_feedback_service
from rail_rag.modules.conversation.application.feedback_service import FeedbackService
from rail_rag.modules.conversation.domain.feedback import FeedbackReaction
from rail_rag.modules.conversation.infrastructure.feedback_repository import FeedbackAnswerNotFoundError
from rail_rag.modules.identity.api.auth_controller import get_current_principal
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.identity.infrastructure.staff_access_repository import IdempotencyConflictError, IdempotencyInProgressError


router = APIRouter(prefix="/messages", tags=["feedback"])


class SubmitFeedbackRequest(BaseModel):
    """用户对单条助手回答提交的赞或踩。"""
    model_config = ConfigDict(extra="forbid")
    reaction: FeedbackReaction


class SubmitFeedbackResponse(BaseModel):
    """反馈保存后的无敏感响应。"""
    message_id: str
    reaction: FeedbackReaction
    expires_at: str
    trace_id: str


@router.post("/{message_id}/feedback", response_model=SubmitFeedbackResponse)
async def submit_feedback(message_id: str, payload: SubmitFeedbackRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)], principal: Annotated[SessionPrincipal, Depends(get_current_principal)], service: Annotated[FeedbackService, Depends(get_feedback_service)]) -> SubmitFeedbackResponse:
    """保存当前主体对自己助手回答的评价；相同回答允许之后改变赞踩。"""
    trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
    try:
        result = await service.submit(principal, message_id, payload.reaction, idempotency_key, trace_id)
    except FeedbackAnswerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant answer was not found") from exc
    except (IdempotencyConflictError, IdempotencyInProgressError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return SubmitFeedbackResponse(message_id=result.message_id, reaction=result.reaction, expires_at=result.expires_at.isoformat(), trace_id=trace_id)
