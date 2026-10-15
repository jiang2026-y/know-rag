"""管理员员工访问接口：限制 leader 修改普通员工状态与部门。

定义 UpdateStaffAccessRequest、StaffAccessResponse、get_current_admin 和 update_staff_access；接口负责鉴权、输入校验和调用身份应用服务。
"""

from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rail_rag.bootstrap import get_staff_access_service
from rail_rag.modules.identity.api.auth_controller import get_current_principal
from rail_rag.modules.identity.application.staff_access_service import StaffAccessService
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.identity.domain.staff_access import StaffAccessUpdate
from rail_rag.modules.identity.infrastructure.staff_access_repository import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    StaffAccessTargetNotOrdinaryError,
    StaffNotFoundError,
)


router = APIRouter(prefix="/admin/staff", tags=["admin"])


class UpdateStaffAccessRequest(BaseModel):
    """管理员可修改的普通员工访问字段；每次请求至少包含一个字段。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ON_JOB", "OFF_JOB"] | None = None
    dept_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_change(self) -> "UpdateStaffAccessRequest":
        """拒绝空更新请求。"""
        if self.status is None and self.dept_id is None:
            raise ValueError("At least one access field is required")
        return self

    def to_command(self) -> StaffAccessUpdate:
        """转换为身份应用层使用的更新命令。"""
        return StaffAccessUpdate(status=self.status, dept_id=self.dept_id)


class StaffAccessResponse(BaseModel):
    """管理员更新成功后返回的员工访问状态。"""

    emp_id: str
    status: Literal["ON_JOB", "OFF_JOB"]
    job: str
    dept_id: int | None
    revoked_sessions: int
    trace_id: str


async def get_current_admin(
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
) -> SessionPrincipal:
    """仅允许当前岗位精确为 leader 的在职员工执行管理操作。"""
    if principal.principal_type != "STAFF" or principal.job != "leader":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access is required")
    return principal


@router.patch("/{emp_id}/access", response_model=StaffAccessResponse)
async def update_staff_access(
    emp_id: str,
    payload: UpdateStaffAccessRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    admin: Annotated[SessionPrincipal, Depends(get_current_admin)],
    service: Annotated[StaffAccessService, Depends(get_staff_access_service)],
) -> StaffAccessResponse:
    """更新员工访问字段，并立即撤销目标员工的 Redis Session。"""
    trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
    try:
        result = await service.update_staff_access(
            admin.principal_id,
            emp_id,
            payload.to_command(),
            idempotency_key,
            trace_id,
        )
    except StaffNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member was not found") from exc
    except StaffAccessTargetNotOrdinaryError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only ordinary staff members can be modified",
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return StaffAccessResponse(
        **result.state.__dict__,
        revoked_sessions=result.revoked_sessions,
        trace_id=trace_id,
    )
