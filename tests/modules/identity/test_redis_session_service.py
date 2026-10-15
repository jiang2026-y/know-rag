"""Redis 服务端会话测试：验证员工会话索引、撤销代次和登出清理。

定义 FakeRedis 与六项测试，分别验证员工与顾客会话撤销、会话代次、登出索引清理和历史会话兼容性。
"""

from __future__ import annotations

import json

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.identity.infrastructure.session_service import (
    InvalidSessionError,
    RedisSessionService,
)


class FakeRedis:
    """提供会话服务测试所需的最小异步 Redis 行为。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.members: dict[str, set[str]] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
            if key in self.members:
                del self.members[key]
                deleted += 1
        return deleted

    async def sadd(self, key: str, *values: str) -> int:
        members = self.members.setdefault(key, set())
        before = len(members)
        members.update(values)
        return len(members) - before

    async def srem(self, key: str, *values: str) -> int:
        members = self.members.get(key, set())
        before = len(members)
        members.difference_update(values)
        if not members:
            self.members.pop(key, None)
        return before - len(members)

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self.values or key in self.members

    async def eval(self, script: str, numkeys: int, *arguments: object) -> int:
        assert numkeys == 2
        epoch_key, sessions_key, ttl_seconds, session_key_prefix = arguments
        assert isinstance(epoch_key, str)
        assert isinstance(sessions_key, str)
        assert isinstance(ttl_seconds, int)
        assert isinstance(session_key_prefix, str)
        current_epoch = int(self.values.get(epoch_key, "0"))
        self.values[epoch_key] = str(current_epoch + 1)
        session_ids = self.members.pop(sessions_key, set())
        for session_id in session_ids:
            self.values.pop(f"{session_key_prefix}{session_id}", None)
        return len(session_ids)


def _service(client: FakeRedis) -> RedisSessionService:
    return RedisSessionService(Settings(auth_session_ttl_seconds=600), client=client)


@pytest.mark.asyncio
async def test_revoke_staff_sessions_deletes_all_active_sessions() -> None:
    client = FakeRedis()
    service = _service(client)
    principal = SessionPrincipal(principal_type="STAFF", principal_id="EMP-001")
    first_session_id = await service.create(principal)
    second_session_id = await service.create(principal)

    revoked_count = await service.revoke_staff_sessions(" EMP-001 ")

    assert revoked_count == 2
    with pytest.raises(InvalidSessionError):
        await service.get_principal(first_session_id)
    with pytest.raises(InvalidSessionError):
        await service.get_principal(second_session_id)


@pytest.mark.asyncio
async def test_staff_session_created_after_revocation_uses_latest_epoch() -> None:
    client = FakeRedis()
    service = _service(client)
    principal = SessionPrincipal(principal_type="STAFF", principal_id="EMP-001")

    await service.revoke_staff_sessions(principal.principal_id)
    session_id = await service.create(principal)

    assert await service.get_principal(session_id) == principal
    stored = json.loads(client.values[service._key(session_id)])
    assert stored["staff_session_epoch"] == 1


@pytest.mark.asyncio
async def test_logout_removes_staff_session_from_revocation_index() -> None:
    client = FakeRedis()
    service = _service(client)
    principal = SessionPrincipal(principal_type="STAFF", principal_id="EMP-001")
    session_id = await service.create(principal)

    await service.delete(session_id)

    assert service._staff_sessions_key("EMP-001") not in client.members
    assert await service.revoke_staff_sessions("EMP-001") == 0


@pytest.mark.asyncio
async def test_customer_session_remains_compatible_with_existing_payload() -> None:
    client = FakeRedis()
    service = _service(client)
    principal = SessionPrincipal(principal_type="CUSTOMER", principal_id="customer-1")
    session_id = await service.create(principal)

    assert await service.get_principal(session_id) == principal


@pytest.mark.asyncio
async def test_revoke_customer_sessions_deletes_all_active_sessions() -> None:
    """顾客密码变更时所有当前索引 Session 均立即失效。"""
    client = FakeRedis()
    service = _service(client)
    principal = SessionPrincipal(principal_type="CUSTOMER", principal_id="customer-1")
    first_session_id = await service.create(principal)
    second_session_id = await service.create(principal)

    revoked_count = await service.revoke_customer_sessions(" customer-1 ")

    assert revoked_count == 2
    with pytest.raises(InvalidSessionError):
        await service.get_principal(first_session_id)
    with pytest.raises(InvalidSessionError):
        await service.get_principal(second_session_id)


@pytest.mark.asyncio
async def test_legacy_customer_session_is_invalid_after_customer_revocation() -> None:
    """未携带新代次的历史顾客 Session 在撤销代次提升后也必须失效。"""
    client = FakeRedis()
    service = _service(client)
    legacy_session_id = "legacy-customer-session"
    client.values[service._key(legacy_session_id)] = json.dumps(
        {"principal": {"principal_type": "CUSTOMER", "principal_id": "customer-1"}}
    )

    assert await service.get_principal(legacy_session_id) == SessionPrincipal(
        principal_type="CUSTOMER", principal_id="customer-1"
    )
    await service.revoke_customer_sessions("customer-1")

    with pytest.raises(InvalidSessionError):
        await service.get_principal(legacy_session_id)
