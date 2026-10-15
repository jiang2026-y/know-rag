"""员工认证仓储测试：验证 P0 固定岗位值约束。

定义两个测试，分别验证 staff、leader 可形成可信主体，以及其他岗位值会被拒绝。
"""

import pytest

from rail_rag.modules.identity.infrastructure.auth_repository import (
    AuthenticationError,
    AuthenticationRepository,
)
from rail_rag.modules.identity.infrastructure.password_hasher import PasswordHasher


@pytest.mark.parametrize("job", ["staff", "leader"])
def test_supported_staff_jobs_create_principal(job: str) -> None:
    """staff 与 leader 是 P0 唯一允许创建员工会话的岗位值。"""
    principal = AuthenticationRepository._staff_principal("EMP-1", job, 1)

    assert principal.job == job


def test_unsupported_staff_job_is_rejected() -> None:
    """避免遗留或未知岗位值获得员工会话。"""
    with pytest.raises(AuthenticationError, match="not supported"):
        AuthenticationRepository._staff_principal("EMP-1", "engineer", 1)


def test_legacy_account_is_marked_for_argon2_upgrade() -> None:
    """旧 PBKDF2 登录结果携带升级标记，但不会把密码放入主体。"""
    hasher = PasswordHasher(
        legacy_pbkdf2_iterations=100000,
        argon2_memory_cost_kib=8192,
        argon2_time_cost=1,
    )
    repository = AuthenticationRepository.__new__(AuthenticationRepository)
    repository._password_hasher = hasher
    account = repository._account_from_row(
        ("EMP-1", hasher.hash_legacy_pbkdf2("correct")),
        "correct",
        "STAFF",
        job="staff",
        dept_id=1,
    )

    assert account is not None
    assert account.needs_password_rehash is True
    assert not hasattr(account, "password")
