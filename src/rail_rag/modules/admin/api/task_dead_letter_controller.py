"""任务死信管理接口：定义仅 leader 可查看和重放的七天保留 DLQ API。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from rail_rag.bootstrap import get_task_dead_letter_service
from rail_rag.modules.admin.api.staff_access_controller import get_current_admin
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.task_dead_letter_service import (
    TaskDeadLetterNotFoundError,
    TaskDeadLetterReplayNotAllowedError,
    TaskDeadLetterService,
)


router = APIRouter(prefix="/admin/tasks/dead-letter", tags=["admin"])


class DeadLetterTaskResponse(BaseModel):
    """管理端展示单条死信任务的最小非敏感字段。"""

    relier_task_id: str
    task_name: str
    reason: str
    resurrections: int = Field(ge=0)
    quarantined_at: datetime
    document_task_id: str | None


@router.get("", response_model=list[DeadLetterTaskResponse])
async def list_dead_letter_tasks(
    limit: int = Query(default=100, ge=1, le=200),
    _: Annotated[SessionPrincipal, Depends(get_current_admin)] = None,
    service: Annotated[TaskDeadLetterService, Depends(get_task_dead_letter_service)] = None,
) -> list[DeadLetterTaskResponse]:
    """仅 leader 可查看当前未过期死信任务；读取前会清理超过七天的记录。"""
    return [DeadLetterTaskResponse(**entry.__dict__) for entry in await service.list_entries(limit)]


@router.post("/{relier_task_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay_dead_letter_task(
    relier_task_id: str,
    _: Annotated[SessionPrincipal, Depends(get_current_admin)] = None,
    service: Annotated[TaskDeadLetterService, Depends(get_task_dead_letter_service)] = None,
) -> dict[str, str]:
    """仅 leader 可将可恢复的文档处理死信重投递到可靠队列。"""
    try:
        await service.replay_document_task(relier_task_id)
    except TaskDeadLetterNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskDeadLetterReplayNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "replay_accepted", "relier_task_id": relier_task_id}
