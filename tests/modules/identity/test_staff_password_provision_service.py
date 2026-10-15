"""HR 初始密码用例测试：验证 Argon2id、幂等重放和 Session 撤销顺序。

定义 FakeProvisionRepository、FakeSessions 与两项测试；测试确保初始密码不进入响应，
且已完成的幂等请求不重复撤销 Session 或写入密码。
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.application.staff_password_provision_service import (
    StaffPasswordProvisionService,
)
from rail_rag.modules.identity.infrastructure.password_hasher import PasswordHasher
from rail_rag.modules.identity.infrastructure.staff_access_repository import IdempotencyReplay
from rail_rag.modules.identity.infrastructure.staff_password_provision_repository import (
    ProvisionedStaff,
)


class FakeProvisionRepository:
    """记录 HR 初始密码用例对仓储的调用，不持久化任何密码。"""

    def __init__(self, replay: IdempotencyReplay | None = None) -> None:
        self.replay = replay
        self.calls: list[str] = []
        self.password_hash: str | None = None

    async def claim_idempotency(self, *_: str) -> IdempotencyReplay | None:
        self.calls.append("claim")
        return self.replay

    async def ensure_eligible(self, _: str) -> None:
        self.calls.append("eligible")

    async def provision_and_complete(
        self, _actor: str, _service: str, emp_id: str, password_hash: str, *_: object
    ) -> ProvisionedStaff:
        self.calls.append("provision")
        self.password_hash = password_hash
        return ProvisionedStaff(emp_id)

    async def release_idempotency(self, *_: str) -> None:
        self.calls.append("release")


class FakeSessions:
    """记录目标员工会话撤销。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def revoke_staff_sessions(self, emp_id: str) -> int:
        self.calls.append(emp_id)
        return 2


def _service(repository: FakeProvisionRepository, sessions: FakeSessions) -> StaffPasswordProvisionService:
    settings = Settings(
        internal_hr_service_token=SecretStr("internal-test-token"),
        auth_argon2_memory_cost_kib=8192,
        auth_argon2_time_cost=1,
    )
    return StaffPasswordProvisionService(
        settings,
        repository,  # type: ignore[arg-type]
        sessions,  # type: ignore[arg-type]
        PasswordHasher(
            argon2_memory_cost_kib=8192,
            argon2_time_cost=1,
        ),
    )


@pytest.mark.asyncio
async def test_provision_initial_password_hashes_and_revokes_sessions() -> None:
    """首次开户撤销 Session 后写入可验证的 Argon2id 摘要。"""
    repository = FakeProvisionRepository()
    sessions = FakeSessions()
    service = _service(repository, sessions)

    result = await service.provision_initial_password(
        " EMP-001 ", "initial-secret", "idem-1", "trace-1"
    )

    assert result.emp_id == "EMP-001"
    assert result.password_format == "ARGON2ID"
    assert result.revoked_sessions == 2
    assert repository.calls == ["claim", "eligible", "provision"]
    assert sessions.calls == ["EMP-001"]
    assert repository.password_hash is not None
    assert repository.password_hash.startswith("$argon2id$")
    assert PasswordHasher(argon2_memory_cost_kib=8192, argon2_time_cost=1).verify(
        "initial-secret", repository.password_hash
    )


@pytest.mark.asyncio
async def test_provision_replays_without_repeating_side_effects() -> None:
    """相同幂等请求仅返回首次响应，不重复撤销或改写密码。"""
    repository = FakeProvisionRepository(
        IdempotencyReplay(
            payload={
                "emp_id": "EMP-001",
                "password_format": "ARGON2ID",
                "revoked_sessions": 1,
            }
        )
    )
    sessions = FakeSessions()

    result = await _service(repository, sessions).provision_initial_password(
        "EMP-001", "initial-secret", "idem-1", "trace-1"
    )

    assert result.revoked_sessions == 1
    assert repository.calls == ["claim"]
    assert sessions.calls == []
