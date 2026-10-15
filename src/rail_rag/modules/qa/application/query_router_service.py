"""查询路由应用服务：校验模型路由并补充已确认的多路召回默认策略。
定义 QueryRouterService，负责模型 JSON 校验、策略白名单、默认组合和安全空路由降级。
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from rail_rag.modules.qa.domain.query_route import QueryRouteResult
from rail_rag.modules.qa.infrastructure.dashscope_query_router_client import QueryRouterClient


class QueryRouterService:
    """将不可信模型文本转换为受控的最多两路数据源路由结果。"""

    def __init__(self, client: QueryRouterClient) -> None:
        self._client = client

    async def route(self, question: str) -> QueryRouteResult | None:
        """调用路由器并补齐默认组合；失败时返回 None，不访问任何数据源。"""
        try:
            raw = await self._client.route(question)
            result = QueryRouteResult.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, ValueError, RuntimeError):
            return None
        if result.confidence < 0.5:
            return None
        strategies = list(result.strategies)
        if result.strategy in {"relational_db", "graph_db"} and "knowledge_base" not in strategies:
            strategies.append("knowledge_base")
        return QueryRouteResult.model_validate(
            {
                **result.model_dump(mode="json"),
                "strategies": strategies[:2],
                "strategy": strategies[0],
            }
        )
