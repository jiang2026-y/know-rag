"""当前车主车辆记录检索适配器：按可信顾客身份只读查询 my_car 白名单字段。
定义 VehicleRecordResult、MyCarRecordRetrieverError 和 MyCarRecordRetriever，禁止接受任意 SQL、任意用户标识或 car_order 作为授权依据。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal


@dataclass(frozen=True)
class VehicleRecordResult:
    """一条经字段最小化后的当前车主车辆证据。"""

    source_id: str
    text: str


class MyCarRecordRetrieverError(RuntimeError):
    """当前车主车辆记录数据源不可用时抛出的受控错误。"""


class MyCarRecordRetriever:
    """仅允许顾客按自己的 user_info.id 查询 my_car，且只返回问题相关字段。"""

    _BASE_COLUMNS = ("car_id", "nickname", "full_name", "plate_number")
    _COLUMN_KEYWORDS = {
        "insurance_expire_date": ("保险",),
        "inspection_expire_date": ("年检", "检车", "检测"),
        "mileage": ("里程", "公里数"),
        "vin": ("车架", "vin"),
        "engine_number": ("发动机号", "发动机编号"),
        "purchase_date": ("购买日期", "购车日期", "什么时候买"),
        "register_date": ("注册日期", "上牌", "登记日期"),
        "color": ("颜色",),
    }
    _ALLOWED_COLUMNS = frozenset((*_BASE_COLUMNS, *_COLUMN_KEYWORDS))

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def search(self, principal: SessionPrincipal, question: str, *, limit: int) -> list[VehicleRecordResult]:
        """按当前可信顾客身份读取其 my_car 记录，不存在绑定或非顾客时返回空结果。"""
        if principal.principal_type != "CUSTOMER" or not principal.principal_id.strip():
            return []
        columns = self._selected_columns(question)
        return await asyncio.to_thread(self._search, principal.principal_id.strip(), columns, limit)

    def _selected_columns(self, question: str) -> tuple[str, ...]:
        normalized = question.lower()
        fields = list(self._BASE_COLUMNS)
        for column, keywords in self._COLUMN_KEYWORDS.items():
            if any(keyword in normalized for keyword in keywords):
                fields.append(column)
        return tuple(dict.fromkeys(fields))

    def _search(self, user_id: str, columns: tuple[str, ...], limit: int) -> list[VehicleRecordResult]:
        if not set(columns).issubset(self._ALLOWED_COLUMNS):
            raise MyCarRecordRetrieverError("Requested vehicle fields are not allowed")
        sql = f"SELECT {', '.join(columns)} FROM my_car WHERE user_id = %s ORDER BY id LIMIT %s"
        try:
            with self._connect() as connection:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(sql, (user_id, limit))
                    rows = cursor.fetchall()
        except pymysql.MySQLError as exc:
            raise MyCarRecordRetrieverError("Unable to read current owner vehicle records") from exc
        return [self._to_result(row, columns) for row in rows]

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

    @staticmethod
    def _to_result(row: dict[str, Any], columns: tuple[str, ...]) -> VehicleRecordResult:
        labels = {
            "nickname": "车辆昵称", "full_name": "车辆名称", "plate_number": "车牌号",
            "insurance_expire_date": "保险到期日", "inspection_expire_date": "年检到期日",
            "mileage": "行驶里程", "vin": "车架号", "engine_number": "发动机号",
            "purchase_date": "购买日期", "register_date": "注册日期", "color": "颜色",
        }
        values: list[str] = []
        for column in columns:
            if column == "car_id" or row.get(column) is None:
                continue
            value = row[column]
            if isinstance(value, (date, datetime)):
                value = value.isoformat()
            values.append(f"{labels.get(column, column)}：{value}")
        source_id = str(row.get("car_id") or "my_car")
        return VehicleRecordResult(source_id=source_id, text="；".join(values) or "当前车辆没有可查询字段")
