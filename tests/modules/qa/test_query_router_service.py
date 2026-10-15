"""查询路由服务测试：验证单一策略校验、低置信度和模型失败时的空路由降级。
定义 FakeRouterClient 与三项异步测试，确保路由失败不会触发任何业务数据源。
"""

import json

import pytest

from rail_rag.modules.qa.application.query_router_service import QueryRouterService


class FakeRouterClient:
    """返回指定 JSON 文本或模拟上游错误。"""

    def __init__(self, payload: dict[str, object] | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    async def route(self, question: str) -> str:
        """返回不可信的模型路由文本。"""
        if self.error is not None:
            raise self.error
        return json.dumps(self.payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_accepts_allowlisted_graph_route() -> None:
    service = QueryRouterService(FakeRouterClient({
        "intent": "查询纯电车型关系",
        "strategy": "graph_db",
        "reasoning": "关注车型和能源类型关系",
        "confidence": 0.95,
    }))

    result = await service.route("纯电车型都有哪些？")

    assert result is not None
    assert result.strategy == "graph_db"
    assert result.strategies == ["graph_db", "knowledge_base"]


@pytest.mark.asyncio
async def test_personal_vehicle_route_adds_knowledge_base_context() -> None:
    service = QueryRouterService(FakeRouterClient({
        "intent": "查询个人保险到期时间",
        "strategy": "relational_db",
        "reasoning": "查询当前车主的结构化车辆数据",
        "confidence": 0.95,
    }))

    result = await service.route("我的保险什么时候到期？")

    assert result is not None
    assert result.strategies == ["relational_db", "knowledge_base"]


@pytest.mark.asyncio
async def test_low_confidence_route_returns_empty_result() -> None:
    service = QueryRouterService(FakeRouterClient({
        "intent": "不确定",
        "strategy": "knowledge_base",
        "reasoning": "信息不足",
        "confidence": 0.49,
    }))

    assert await service.route("帮我看看") is None


@pytest.mark.asyncio
async def test_invalid_route_or_model_failure_returns_empty_result() -> None:
    invalid = QueryRouterService(FakeRouterClient({
        "intent": "越权",
        "strategy": "any_database",
        "reasoning": "非法策略",
        "confidence": 1.0,
    }))
    failed = QueryRouterService(FakeRouterClient(error=RuntimeError("timeout")))

    assert await invalid.route("查询车辆") is None
    assert await failed.route("查询车辆") is None
