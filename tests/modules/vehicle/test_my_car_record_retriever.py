"""当前车主车辆记录适配器测试：验证非顾客无法触发 my_car 查询及字段最小化选择。
定义两项测试，确保授权依据仅为可信顾客 Session 与 my_car.user_id。
"""

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.vehicle.infrastructure.my_car_record_retriever import MyCarRecordRetriever


@pytest.mark.asyncio
async def test_staff_cannot_query_customer_my_car_records() -> None:
    retriever = MyCarRecordRetriever(Settings())
    staff = SessionPrincipal(principal_type="STAFF", principal_id="EMP-1", job="staff")

    assert await retriever.search(staff, "我的保险什么时候到期？", limit=5) == []


def test_selects_only_question_relevant_vehicle_fields() -> None:
    retriever = MyCarRecordRetriever(Settings())

    columns = retriever._selected_columns("我的保险和年检什么时候到期？")

    assert "insurance_expire_date" in columns
    assert "inspection_expire_date" in columns
    assert "vin" not in columns
