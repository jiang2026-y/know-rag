"""管理员员工访问接口测试：验证 leader 白名单和请求模型约束。

定义两项测试，分别验证管理员岗位判定和空更新拒绝规则。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from rail_rag.modules.admin.api.staff_access_controller import (
    UpdateStaffAccessRequest,
    get_current_admin,
)
from rail_rag.modules.identity.domain.principal import SessionPrincipal


@pytest.mark.asyncio
async def test_only_leader_staff_can_be_admin() -> None:
    principal = SessionPrincipal(
        principal_type="STAFF", principal_id="EMP-1", job="leader", dept_id=1
    )
    assert await get_current_admin(principal) == principal

    with pytest.raises(HTTPException) as error:
        await get_current_admin(
            SessionPrincipal(
                principal_type="STAFF", principal_id="EMP-2", job="staff", dept_id=1
            )
        )
    assert error.value.status_code == 403


def test_empty_staff_access_update_is_rejected() -> None:
    with pytest.raises(ValueError):
        UpdateStaffAccessRequest()


def test_job_is_not_an_updatable_staff_access_field() -> None:
    with pytest.raises(ValidationError):
        UpdateStaffAccessRequest(job="leader")
