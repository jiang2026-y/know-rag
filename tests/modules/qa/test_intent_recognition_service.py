"""意图识别应用服务测试：验证严格 Schema、一次重试与安全降级路由。

定义 FakeIntentClient 及三个测试，分别覆盖有效结果、Schema 修正重试和连续失败降级。
"""

import json

import pytest

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.qa.application.intent_recognition_service import (
    IntentRecognitionService,
)


class FakeIntentClient:
    """按调用顺序返回模型文本，并记录服务端传入的角色和重试标志。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str, bool]] = []

    async def recognize(self, question: str, user_role: str, *, retry: bool = False) -> str:
        self.calls.append((question, user_role, retry))
        return next(self.responses)


def staff_principal() -> SessionPrincipal:
    """创建用于角色传递测试的可信普通员工主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id="EMP-1", job="staff")


def valid_payload(**overrides: object) -> str:
    """构造符合当前七类意图和八实体契约的最小模型 JSON。"""
    payload: dict[str, object] = {
        "related": True,
        "intent": "车辆使用与技术指导",
        "entities": {
            "car_model": None,
            "order_id": None,
            "dealer": None,
            "fault_description": None,
            "appointment_time": None,
            "part_name": None,
            "function_name": "自适应巡航",
            "warning_light": None,
        },
        "reasoning": "用户询问车辆功能操作方法。",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_returns_schema_valid_result_and_trusted_role() -> None:
    client = FakeIntentClient([valid_payload()])
    result = await IntentRecognitionService(client).recognize(staff_principal(), "自适应巡航怎么用？")

    assert result.related is True
    assert result.entities.function_name == "自适应巡航"
    assert client.calls == [("自适应巡航怎么用？", "staff", False)]


@pytest.mark.asyncio
async def test_retries_with_backoff_when_first_response_fails_schema_validation() -> None:
    client = FakeIntentClient(["不是 JSON", valid_payload()])
    result = await IntentRecognitionService(client).recognize(staff_principal(), "自适应巡航怎么用？")

    assert result.intent == "车辆使用与技术指导"
    assert result.status == "RETRY_SUCCESS"
    assert result.attempts == 2
    assert [call[2] for call in client.calls] == [False, True]


@pytest.mark.asyncio
async def test_falls_back_to_general_conversation_after_three_invalid_responses() -> None:
    client = FakeIntentClient(["{}", "[]", "不是 JSON"])
    result = await IntentRecognitionService(client).recognize(staff_principal(), "今天天气怎么样？")

    assert result.related is False
    assert result.intent == "闲聊与通用问答"
    assert result.status == "FALLBACK"
    assert result.attempts == 3
    assert result.entities.model_dump() == {
        "car_model": None,
        "order_id": None,
        "dealer": None,
        "fault_description": None,
        "appointment_time": None,
        "part_name": None,
        "function_name": None,
        "warning_light": None,
    }
