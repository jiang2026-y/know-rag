"""车辆范围应用服务测试：验证当前车主范围、员工最小权限和越权拒绝。

定义 FakeVehicleScopeRepository 及三项测试，覆盖顾客车辆范围查询、员工空范围与非本人车辆拒绝。
"""

from __future__ import annotations

import pytest

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.vehicle.application.vehicle_scope_service import VehicleScopeService
from rail_rag.modules.vehicle.domain.vehicle_scope import VehicleAccessDeniedError


class FakeVehicleScopeRepository:
    """按用户 ID 返回预设车辆集合，替代 MySQL 只读查询。"""

    def __init__(self, vehicle_ids_by_user: dict[str, frozenset[str]]) -> None:
        self._vehicle_ids_by_user = vehicle_ids_by_user
        self.requested_user_ids: list[str] = []

    async def list_vehicle_ids_for_owner(self, user_id: str) -> frozenset[str]:
        self.requested_user_ids.append(user_id)
        return self._vehicle_ids_by_user.get(user_id, frozenset())


@pytest.mark.asyncio
async def test_customer_scope_uses_only_current_owner_vehicle_ids() -> None:
    repository = FakeVehicleScopeRepository({"101": frozenset({"CAR-1", "CAR-2"})})
    service = VehicleScopeService(repository)  # type: ignore[arg-type]
    principal = SessionPrincipal(principal_type="CUSTOMER", principal_id="101")

    scope = await service.get_scope(principal)

    assert scope.vehicle_ids == frozenset({"CAR-1", "CAR-2"})
    assert scope.require("CAR-1") == "CAR-1"
    assert repository.requested_user_ids == ["101"]


@pytest.mark.asyncio
async def test_staff_member_receives_no_vehicle_scope() -> None:
    repository = FakeVehicleScopeRepository({"101": frozenset({"CAR-1"})})
    service = VehicleScopeService(repository)  # type: ignore[arg-type]
    principal = SessionPrincipal(
        principal_type="STAFF", principal_id="EMP-1", job="leader", dept_id=1
    )

    scope = await service.get_scope(principal)

    assert scope.vehicle_ids == frozenset()
    assert repository.requested_user_ids == []


@pytest.mark.asyncio
async def test_customer_cannot_access_another_owner_vehicle() -> None:
    repository = FakeVehicleScopeRepository({"101": frozenset({"CAR-1"})})
    service = VehicleScopeService(repository)  # type: ignore[arg-type]
    scope = await service.get_scope(
        SessionPrincipal(principal_type="CUSTOMER", principal_id="101")
    )

    with pytest.raises(VehicleAccessDeniedError, match="Vehicle access is not permitted"):
        scope.require("CAR-OTHER")
