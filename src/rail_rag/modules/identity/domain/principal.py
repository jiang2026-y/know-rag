"""认证接口模型：定义客户/员工登录请求、登录响应与可信身份主体。

定义 CustomerLoginRequest、StaffLoginRequest、LoginResponse 和 SessionPrincipal，
分别用于接收凭证、返回会话信息及在后端传递已验真的身份和员工权限快照。
"""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field


PrincipalType = Literal["CUSTOMER", "STAFF"]


class CustomerLoginRequest(BaseModel):
    """客户使用手机号和密码创建服务端会话。"""

    phone: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=256)


class StaffLoginRequest(BaseModel):
    """员工使用工号和密码创建服务端会话。"""

    emp_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CustomerPasswordChangeRequest(BaseModel):
    """顾客修改密码所需的当前密码和新密码。"""

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class SessionPrincipal(BaseModel):
    """已由服务端会话验证的身份主体及员工权限快照，禁止由客户端直接提交。"""

    principal_type: PrincipalType
    principal_id: str = Field(min_length=1, max_length=64)
    job: str | None = Field(default=None, min_length=1, max_length=128)
    dept_id: int | None = Field(default=None, ge=1)


class LoginResponse(BaseModel):
    """登录成功后的非敏感身份和会话有效期信息。"""

    principal: SessionPrincipal
    expires_in_seconds: int


def conversation_owner_id(principal: SessionPrincipal) -> str:
    """生成固定长度的会话归属键，避免客户和员工的原始 ID 发生碰撞。"""
    payload = f"{principal.principal_type}\x00{principal.principal_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
