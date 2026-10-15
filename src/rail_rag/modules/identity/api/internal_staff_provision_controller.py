"""HR 内部开户接口：验证服务令牌并为已存在员工设置一次性初始密码。

定义 InitialPasswordRequest、InitialPasswordResponse、verify_hr_service_token 与
provision_initial_password；浏览器和普通员工 Session 均不能调用此内部接口。
"""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from rail_rag.bootstrap import get_staff_password_provision_service
from rail_rag.config.settings import Settings, get_settings
from rail_rag.modules.identity.application.staff_password_provision_service import (
    StaffPasswordProvisionResult,
    StaffPasswordProvisionService,
)
from rail_rag.modules.identity.infrastructure.staff_access_repository import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
)
from rail_rag.modules.identity.infrastructure.staff_password_provision_repository import (
    ProvisioningStaffNotEligibleError,
    ProvisioningStaffNotFoundError,
)


router = APIRouter(prefix="/internal/v1/hr/staff", tags=["internal-hr"])


class InitialPasswordRequest(BaseModel):
    """HR 服务为已创建员工提交的初始密码；只在本次请求内存中存在。"""

    model_config = ConfigDict(extra="forbid")

    initial_password: str = Field(min_length=1, max_length=256)


class InitialPasswordResponse(BaseModel):
    """内部开户成功后的无敏感响应。"""

    emp_id: str
    password_format: str
    revoked_sessions: int
    trace_id: str


async def verify_hr_service_token(
    token: Annotated[str | None, Header(alias="X-Internal-Service-Token")] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    """仅接受配置的 HR 服务令牌；未配置时安全地禁用内部接口。"""
    configured = settings.internal_hr_service_token
    expected = configured.get_secret_value().strip() if configured else ""
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal HR provisioning is not configured",
        )
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service token",
        )


@router.post(
    "/{emp_id}/initial-password",
    response_model=InitialPasswordResponse,
    dependencies=[Depends(verify_hr_service_token)],
)
async def provision_initial_password(
    emp_id: str,
    payload: InitialPasswordRequest,
    request: Request,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
    service: Annotated[
        StaffPasswordProvisionService, Depends(get_staff_password_provision_service)
    ],
) -> InitialPasswordResponse:
    """为 HR 已创建、在职且空密码的员工保存 Argon2id 初始密码。"""
    trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
    try:
        result = await service.provision_initial_password(
            emp_id, payload.initial_password, idempotency_key, trace_id
        )
    except ProvisioningStaffNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member was not found") from exc
    except ProvisioningStaffNotEligibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Staff member is not eligible for initial password provisioning",
        ) from exc
    except (IdempotencyConflictError, IdempotencyInProgressError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _response(result, trace_id)


def _response(result: StaffPasswordProvisionResult, trace_id: str) -> InitialPasswordResponse:
    """将应用层结果映射为不含密码信息的 HTTP 响应。"""
    return InitialPasswordResponse(**result.as_payload(), trace_id=trace_id)
