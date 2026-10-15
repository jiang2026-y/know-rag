"""员工访问应用服务测试：验证权限更新、幂等重放与撤销调用顺序。

定义 FakeRepository、FakeSessions 及两项测试，分别验证首次更新和重复幂等请求不会重复撤销 Session。
"""

from __future__ import annotations

from typing import cast

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.application.staff_access_service import StaffAccessService
from rail_rag.modules.identity.domain.staff_access import StaffAccessState, StaffAccessUpdate
from rail_rag.modules.identity.infrastructure.staff_access_repository import (
    IdempotencyReplay,
    StaffAccessRepository,
    StaffAccessTargetNotOrdinaryError,
)


class FakeRepository:
    """提供员工访问应用服务所需的内存仓储行为。"""

    def __init__(self) -> None:
        self.replay: IdempotencyReplay | None = None
        self.completed: dict[str, object] | None = None
        self.update_calls = 0

    @staticmethod
    def request_digest(target_emp_id: str, update: StaffAccessUpdate) -> str:
        return f"{target_emp_id}:{update.status}:{update.dept_id}"

    async def claim_idempotency(self, actor: str, key: str, digest: str):
        return self.replay

    async def update_staff_access(self, actor, target, update, trace_id):
        self.update_calls += 1
        return StaffAccessState(target, update.status or "ON_JOB", "staff", update.dept_id or 1)

    async def complete_idempotency(self, actor, key, payload):
        self.completed = payload

    async def release_idempotency(self, actor, key):
        raise AssertionError("release should not be called in this test")


class FakeSessions:
    """记录员工 Session 撤销调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def revoke_staff_sessions(self, emp_id: str) -> int:
        self.calls.append(emp_id)
        return 2


class LeaderTargetCursor:
    """模拟返回 leader 目标员工的 MySQL 游标，并记录执行过的 SQL。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def __enter__(self) -> "LeaderTargetCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, _: object) -> None:
        self.queries.append(query)

    @staticmethod
    def fetchone() -> tuple[object, ...]:
        return ("EMP-2", "ON_JOB", "leader", 1)


class LeaderTargetConnection:
    """模拟员工访问更新所需的 MySQL 连接。"""

    def __init__(self) -> None:
        self.cursor_instance = LeaderTargetCursor()
        self.committed = False

    def __enter__(self) -> "LeaderTargetConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> LeaderTargetCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_update_staff_access_persists_and_revokes_sessions() -> None:
    repository = FakeRepository()
    sessions = FakeSessions()
    service = StaffAccessService(repository, sessions)  # type: ignore[arg-type]

    result = await service.update_staff_access(
        "LEADER-1",
        "EMP-1",
        StaffAccessUpdate(status="OFF_JOB"),
        "request-1",
        "trace-1",
    )

    assert result.state.status == "OFF_JOB"
    assert result.revoked_sessions == 2
    assert sessions.calls == ["EMP-1"]
    assert repository.completed is not None


@pytest.mark.asyncio
async def test_replayed_idempotency_response_does_not_repeat_update_or_revoke() -> None:
    repository = FakeRepository()
    sessions = FakeSessions()
    first = StaffAccessService(repository, sessions)  # type: ignore[arg-type]
    first_result = await first.update_staff_access(
        "LEADER-1", "EMP-1", StaffAccessUpdate(dept_id=2), "request-1", "trace-1"
    )
    repository.replay = IdempotencyReplay(first_result.as_payload())

    replay = await first.update_staff_access(
        "LEADER-1", "EMP-1", StaffAccessUpdate(dept_id=2), "request-1", "trace-2"
    )

    assert replay == first_result
    assert repository.update_calls == 1
    assert sessions.calls == ["EMP-1"]


def test_repository_does_not_update_a_leader_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """领导不能通过该接口修改另一名领导，且不得执行 UPDATE。"""
    repository = StaffAccessRepository(cast(Settings, object()))
    connection = LeaderTargetConnection()
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    with pytest.raises(StaffAccessTargetNotOrdinaryError):
        repository._update_staff_access(
            "LEADER-1", "EMP-2", StaffAccessUpdate(status="OFF_JOB"), "trace-1"
        )

    assert connection.committed is False
    assert not any("UPDATE staff_info" in query for query in connection.cursor_instance.queries)
