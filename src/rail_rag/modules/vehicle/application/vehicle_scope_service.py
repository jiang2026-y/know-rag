"""车辆范围应用服务：从可信顾客主体构造只读车辆访问范围。

定义 VehicleScopeService；服务只为顾客查询 my_car.user_id 绑定车辆，员工和非顾客主体一律获得空车辆范围。
"""

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.vehicle.domain.vehicle_scope import VehicleScope
from rail_rag.modules.vehicle.infrastructure.vehicle_scope_repository import (
    VehicleScopeRepository,
)


class VehicleScopeService:
    """构造供后续问答工具使用的最小车辆访问范围。"""

    def __init__(self, repository: VehicleScopeRepository) -> None:
        self._repository = repository

    async def get_scope(self, principal: SessionPrincipal) -> VehicleScope:
        """只为顾客主体读取当前车辆范围，其他主体默认最小权限。"""
        if principal.principal_type != "CUSTOMER":
            return VehicleScope(owner_user_id=principal.principal_id, vehicle_ids=frozenset())
        vehicle_ids = await self._repository.list_vehicle_ids_for_owner(
            principal.principal_id
        )
        return VehicleScope(owner_user_id=principal.principal_id, vehicle_ids=vehicle_ids)
