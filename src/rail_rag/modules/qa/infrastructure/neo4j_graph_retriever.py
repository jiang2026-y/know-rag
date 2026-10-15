"""Neo4j 公开图谱检索适配器：仅执行参数化、白名单的只读车型与配件查询。
定义 GraphRetrievalResult、Neo4jGraphRetrieverError 和 Neo4jGraphRetriever；禁止接收模型生成的 Cypher 或执行任意写操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

from rail_rag.config.settings import Settings


@dataclass(frozen=True)
class GraphRetrievalResult:
    """一条经过字段白名单转换的公开车型图谱证据。"""

    source_id: str
    text: str


class Neo4jGraphRetrieverError(RuntimeError):
    """Neo4j 不可用或返回数据不符合预期时抛出的受控错误。"""


class Neo4jGraphRetriever:
    """读取公开车型图谱，不接收或执行任何由模型生成的 Cypher。"""

    _SEARCH_CYPHER = """
    MATCH (brand:Brand)-[:HAS_MODEL]->(model:CarModel)-[:HAS_VERSION]->(version:CarVersion)
    OPTIONAL MATCH (version)-[:HAS_ENERGY_TYPE]->(energy:EnergyType)
    OPTIONAL MATCH (version)-[:USES_PART]->(part:Part)
    WITH brand, model, version, energy, collect(DISTINCT part.name)[0..8] AS parts, toLower($question) AS normalized_question
    WHERE normalized_question CONTAINS toLower(model.name)
       OR any(alias IN coalesce(model.aliases, []) WHERE normalized_question CONTAINS toLower(alias))
       OR any(alias IN coalesce(brand.aliases, []) WHERE normalized_question CONTAINS toLower(alias))
       OR (normalized_question CONTAINS '纯电' AND energy.name = '纯电动')
       OR (normalized_question CONTAINS '插混' AND energy.name = '插电混动')
       OR (normalized_question CONTAINS '燃油' AND energy.name = '燃油')
       OR any(part_name IN parts WHERE normalized_question CONTAINS toLower(part_name))
    RETURN version.infoId AS info_id, brand.name AS brand_name, model.name AS model_name,
           version.name AS version_name, version.modelYear AS model_year, version.version AS version,
           version.rangeKm AS range_km, version.guidePrice AS guide_price,
           energy.name AS energy_name, parts
    ORDER BY version.infoId
    LIMIT $limit
    """

    def __init__(self, settings: Settings, *, driver: AsyncDriver | None = None) -> None:
        self._enabled = settings.neo4j_enabled
        self._database = settings.neo4j_database
        self._driver = driver
        if driver is None and self._enabled and settings.neo4j_password is not None:
            password = settings.neo4j_password.get_secret_value().strip()
            if password:
                self._driver = AsyncGraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, password),
                    connection_timeout=settings.neo4j_connect_timeout_seconds,
                )

    async def search(self, question: str, *, limit: int) -> list[GraphRetrievalResult]:
        """以参数化查询检索公开图谱；图服务不可用时安全返回空结果。"""
        if not self._enabled or self._driver is None:
            return []
        message = question.strip()
        if not message:
            return []
        try:
            async with self._driver.session(database=self._database, default_access_mode="READ") as session:
                records = await session.execute_read(self._run_search, message, limit)
        except Exception as exc:
            raise Neo4jGraphRetrieverError("Neo4j graph retrieval is unavailable") from exc
        return [self._to_result(record) for record in records]

    @classmethod
    async def _run_search(cls, tx: Any, question: str, limit: int) -> list[Any]:
        result = await tx.run(cls._SEARCH_CYPHER, question=question, limit=limit)
        return [record async for record in result]

    @staticmethod
    def _to_result(record: Any) -> GraphRetrievalResult:
        data = record.data() if hasattr(record, "data") else dict(record)
        info_id = str(data.get("info_id") or "unknown")
        parts = [str(item) for item in data.get("parts") or [] if item]
        fields = [
            f"品牌：{data.get('brand_name') or '未知'}",
            f"车型：{data.get('model_name') or '未知'}",
            f"版本：{data.get('version_name') or data.get('version') or '未知'}",
        ]
        if data.get("model_year") is not None:
            fields.append(f"年款：{data['model_year']}")
        if data.get("energy_name"):
            fields.append(f"能源类型：{data['energy_name']}")
        if data.get("range_km") is not None:
            fields.append(f"续航：{data['range_km']} km")
        if data.get("guide_price") is not None:
            fields.append(f"指导价：{data['guide_price']} 万元")
        if parts:
            fields.append(f"关联配件：{'、'.join(parts)}")
        return GraphRetrievalResult(source_id=info_id, text="；".join(fields))
