"""查询路由领域模型：定义受控数据源策略及多路召回路由结果。
定义 QueryRouteStrategy、QueryRouteResult 和空路由降级结果，兼容旧版单策略输出。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


QueryRouteStrategy = Literal["relational_db", "graph_db", "knowledge_base"]


class QueryRouteResult(BaseModel):
    """承载路由器返回的一个或多个数据源策略和简短判断摘要。"""

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1, max_length=128)
    strategies: list[QueryRouteStrategy] = Field(default_factory=list, min_length=1, max_length=2)
    # 保留旧字段，便于已存在的客户端和历史测试平滑升级。
    strategy: QueryRouteStrategy | None = None
    reasoning: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_strategy(cls, value: object) -> object:
        """把旧版 strategy 单值转换为新版 strategies 列表。"""
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        strategies = payload.get("strategies")
        strategy = payload.get("strategy")
        if strategies is None and strategy is not None:
            payload["strategies"] = [strategy]
        elif strategies is not None and strategy is None and isinstance(strategies, list) and strategies:
            payload["strategy"] = strategies[0]
        return payload

    @model_validator(mode="after")
    def validate_strategy_consistency(self) -> "QueryRouteResult":
        """确保列表不重复，且兼容字段与首选路由一致。"""
        unique = list(dict.fromkeys(self.strategies))
        if len(unique) != len(self.strategies):
            self.strategies = unique
        if not self.strategies:
            raise ValueError("At least one query route strategy is required")
        if self.strategy is None:
            self.strategy = self.strategies[0]
        elif self.strategy != self.strategies[0]:
            raise ValueError("strategy must match the first strategies item")
        return self


def empty_query_route() -> None:
    """表示路由模型失败或结果不可信时不调用任何业务数据源。"""

    return None
