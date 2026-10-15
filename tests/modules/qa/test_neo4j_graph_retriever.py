"""Neo4j 图检索适配器测试：验证禁用时不连接图数据库及公开字段的证据转换。
定义假记录与两项异步测试，确保适配器不依赖模型生成 Cypher。
"""

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.qa.infrastructure.neo4j_graph_retriever import Neo4jGraphRetriever


@pytest.mark.asyncio
async def test_disabled_graph_retriever_does_not_connect() -> None:
    retriever = Neo4jGraphRetriever(Settings(neo4j_enabled=False))

    assert await retriever.search("纯电车型都有哪些？", limit=5) == []


def test_converts_only_public_graph_fields() -> None:
    class FakeRecord:
        def data(self):
            return {
                "info_id": "CI_001", "brand_name": "Tesla", "model_name": "Model 3",
                "version_name": "Model 3 2025 焕新版", "model_year": 2025,
                "energy_name": "纯电动", "range_km": 713, "guide_price": 27.19,
                "parts": ["轮胎", "电池"],
            }

    result = Neo4jGraphRetriever._to_result(FakeRecord())

    assert result.source_id == "CI_001"
    assert "品牌：Tesla" in result.text
    assert "关联配件：轮胎、电池" in result.text
