"""车辆范围仓储：只读查询当前顾客在 my_car 中绑定的车辆 ID。

定义 VehicleScopeRepositoryError 和 VehicleScopeRepository；仓储只以 my_car.user_id 作为车辆授权依据，不使用订单表授予访问权限。
"""

import asyncio

import pymysql

from rail_rag.config.settings import Settings


class VehicleScopeRepositoryError(RuntimeError):
    """车辆范围数据源不可用时抛出。"""


class VehicleScopeRepository:
    """从 MySQL 的 my_car 表读取单一当前车主的车辆范围。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def list_vehicle_ids_for_owner(self, user_id: str) -> frozenset[str]:
        """返回当前绑定到指定 user_info.id 的全部车辆 ID。"""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            return frozenset()
        return await asyncio.to_thread(self._list_vehicle_ids_for_owner, normalized_user_id)

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password.get_secret_value(),
            database=self._settings.mysql_database,
            charset="utf8mb4",
            connect_timeout=self._settings.mysql_connect_timeout_seconds,
        )

    def _list_vehicle_ids_for_owner(self, user_id: str) -> frozenset[str]:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT car_id
                        FROM my_car
                        WHERE user_id = %s AND car_id IS NOT NULL
                        ORDER BY car_id
                        """,
                        (user_id,),
                    )
                    rows = cursor.fetchall()
        except pymysql.MySQLError as exc:
            raise VehicleScopeRepositoryError("Unable to read vehicle access scope") from exc
        return frozenset(
            str(row[0]).strip()
            for row in rows
            if row and row[0] is not None and str(row[0]).strip()
        )
