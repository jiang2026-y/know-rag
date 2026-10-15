"""认证 HTTP 接口：创建/撤销服务端会话，并提供可信身份依赖。

定义 customer_login、staff_login、logout、get_current_principal 和相关服务工厂；
登录成功后仅向浏览器下发 HttpOnly 会话 Cookie。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from rail_rag.bootstrap import (
    get_authentication_repository,
    get_password_hasher,
    get_session_service,
)
from rail_rag.modules.identity.domain.principal import (
    CustomerPasswordChangeRequest,
    CustomerLoginRequest,
    LoginResponse,
    SessionPrincipal,
    StaffLoginRequest,
)
from rail_rag.modules.identity.infrastructure.auth_repository import AuthenticationRepository
from rail_rag.modules.identity.infrastructure.session_service import (
    InvalidSessionError,
    RedisSessionService,
)
from rail_rag.config.settings import Settings, get_settings


router = APIRouter(prefix="/auth", tags=["auth"])

async def get_current_principal(
    request: Request,
    service: Annotated[RedisSessionService, Depends(get_session_service)],
    repository: Annotated[
        AuthenticationRepository, Depends(get_authentication_repository)
    ],
) -> SessionPrincipal:
    """从 Session 读取主体，并实时校验员工的在职状态、岗位和部门。"""
    session_id = request.cookies.get(get_settings().auth_session_cookie_name)
    principal = await service.get_principal(session_id)
    if principal.principal_type != "STAFF":
        return principal
    current_principal = await repository.get_active_staff_principal(principal.principal_id)
    if current_principal != principal:
        await service.delete(session_id)
        raise InvalidSessionError("Staff authorization has changed; please sign in again")
    return current_principal


@router.post("/customer/sessions", response_model=LoginResponse)
async def customer_login(
    payload: CustomerLoginRequest,
    response: Response,
    repository: Annotated[
        AuthenticationRepository, Depends(get_authentication_repository)
    ],
    sessions: Annotated[RedisSessionService, Depends(get_session_service)],
) -> LoginResponse:
    """验证客户 Argon2id/旧 PBKDF2 密码、升级旧摘要并创建 Redis Session。"""
    account = await repository.authenticate_customer(payload.phone, payload.password)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    await repository.upgrade_password_hash(account, payload.password)
    return await _login_response(response, account.principal, sessions, get_settings())


@router.post("/staff/sessions", response_model=LoginResponse)
async def staff_login(
    payload: StaffLoginRequest,
    response: Response,
    repository: Annotated[
        AuthenticationRepository, Depends(get_authentication_repository)
    ],
    sessions: Annotated[RedisSessionService, Depends(get_session_service)],
) -> LoginResponse:
    """验证在职员工 Argon2id/旧 PBKDF2 密码、升级旧摘要并创建 Redis Session。"""
    account = await repository.authenticate_staff(payload.emp_id, payload.password)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    await repository.upgrade_password_hash(account, payload.password)
    return await _login_response(response, account.principal, sessions, get_settings())


@router.patch("/customer/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_customer_password(
    payload: CustomerPasswordChangeRequest,
    response: Response,
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    repository: Annotated[
        AuthenticationRepository, Depends(get_authentication_repository)
    ],
    sessions: Annotated[RedisSessionService, Depends(get_session_service)],
) -> Response:
    """修改顾客密码；更新前撤销该顾客全部 Redis Session，旧登录态立即失效。"""
    if principal.principal_type != "CUSTOMER":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer account required")
    account = await repository.authenticate_customer_by_id(
        principal.principal_id, payload.current_password
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid current password")
    await sessions.revoke_customer_sessions(principal.principal_id)
    await repository.update_customer_password(account, payload.new_password)
    response.delete_cookie(get_settings().auth_session_cookie_name, path="/")
    return response


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    request: Request,
    sessions: Annotated[RedisSessionService, Depends(get_session_service)],
) -> Response:
    """撤销当前 Redis Session 并清理浏览器 Cookie。"""
    session_id = request.cookies.get(get_settings().auth_session_cookie_name)
    await sessions.delete(session_id)
    response.delete_cookie(get_settings().auth_session_cookie_name, path="/")
    return response


async def _login_response(
    response: Response,
    principal: SessionPrincipal,
    sessions: RedisSessionService,
    settings: Settings,
) -> LoginResponse:
    """创建 Session 并以安全 Cookie 下发其随机标识。"""
    session_id = await sessions.create(principal)
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=session_id,
        max_age=sessions.ttl_seconds,
        httponly=True,
        secure=settings.auth_session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return LoginResponse(principal=principal, expires_in_seconds=sessions.ttl_seconds)
