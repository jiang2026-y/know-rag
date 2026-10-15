"""车辆访问领域规则：表示顾客当前可访问车辆，并拒绝越权车辆请求。

定义 VehicleAccessDeniedError 和 VehicleScope；前者表示车辆不属于当前顾客，后者负责保存并判断授权车辆 ID 集合。
"""

from dataclasses import dataclass


class VehicleAccessDeniedError(PermissionError):
    """请求车辆不属于当前顾客时抛出，避免泄露车辆是否存在。"""


@dataclass(frozen=True)
class VehicleScope:
    """由 `my_car.user_id` 推导出的单一顾客当前车辆范围。"""

    owner_user_id: str
    vehicle_ids: frozenset[str]

    def allows(self, car_id: str) -> bool:
        """判断给定车辆 ID 是否在当前顾客范围内。"""
        return car_id.strip() in self.vehicle_ids

    def require(self, car_id: str) -> str:
        """返回已授权车辆 ID；未授权时返回不泄露资源存在性的拒绝。"""
        normalized_car_id = car_id.strip()
        if not normalized_car_id or normalized_car_id not in self.vehicle_ids:
            raise VehicleAccessDeniedError("Vehicle access is not permitted")
        return normalized_car_id
