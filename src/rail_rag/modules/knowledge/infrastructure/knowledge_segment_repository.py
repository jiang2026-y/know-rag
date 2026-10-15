"""转换后 Markdown 分片的 MySQL 持久化仓储：分片写入、向量化读取与二次授权查询。

定义 StoredKnowledgeSegment、AuthorizedKnowledgeSegment 和 KnowledgeSegmentRepository；
仓储提供分片替换、待向量化分页读取、按 chunk_id 的二次授权、父块回溯所需元数据查询
以及供缓存未命中时回源的正文查询。
"""

import asyncio
import json
from dataclasses import dataclass
from pymysql.cursors import DictCursor
from typing import Any, Iterable

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import MarkdownChunk


class KnowledgeSegmentRepositoryError(RuntimeError):
    """知识分片持久化失败时抛出。"""


@dataclass(frozen=True)
class StoredKnowledgeSegment:
    """从 MySQL 读取、等待写入向量索引的子分片。"""

    id: int
    chunk_id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AuthorizedKnowledgeSegment:
    """已按文档当前状态和可见标签二次校验的知识分片。"""

    chunk_id: str
    text: str
    metadata: dict[str, Any]
    document_id: int
    document_version: int
    chunk_order: int
    document_title: str = ""


class KnowledgeSegmentRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def replace_for_document(
        self,
        document_id: int,
        document_version: int,
        chunks: Iterable[MarkdownChunk],
    ) -> None:
        await asyncio.to_thread(
            self._replace_for_document, document_id, document_version, list(chunks)
        )

    async def list_pending_for_embedding(
        self, document_id: int, document_version: int, *, limit: int = 100
    ) -> list[StoredKnowledgeSegment]:
        """读取一页尚未向量化的非父级分片。"""
        return await asyncio.to_thread(
            self._list_pending_for_embedding, document_id, document_version, limit
        )

    async def mark_vectors_stored(
        self, embeddings: Iterable[tuple[int, str]]
    ) -> None:
        """在 ES 批量写入成功后，保存对应的 Elasticsearch 文档 ID。"""
        rows = list(embeddings)
        if not rows:
            return
        await asyncio.to_thread(self._mark_vectors_stored, rows)

    async def get_by_chunk_ids(self, chunk_ids: list[str]) -> list[StoredKnowledgeSegment]:
        """按 ES 命中或父分片 ID 读取完整 Markdown 上下文。"""
        if not chunk_ids:
            return []
        return await asyncio.to_thread(self._get_by_chunk_ids, chunk_ids)

    async def get_authorized_by_chunk_ids(
        self, chunk_ids: list[str], accessible_by: list[str]
    ) -> list[AuthorizedKnowledgeSegment]:
        """按 MySQL 当前文档状态和标签重新读取 ES 命中或其上下文分片。"""
        if not chunk_ids or not accessible_by:
            return []
        return await asyncio.to_thread(
            self._get_authorized_by_chunk_ids, chunk_ids, accessible_by
        )

    async def get_authorized_refs_by_chunk_ids(
        self, chunk_ids: list[str], accessible_by: list[str]
    ) -> list[AuthorizedKnowledgeSegment]:
        """只读取二次授权结论与分片元数据，正文改由缓存或正文查询补齐。"""
        if not chunk_ids or not accessible_by:
            return []
        return await asyncio.to_thread(
            self._get_authorized_refs_by_chunk_ids, chunk_ids, accessible_by
        )

    async def get_text_by_chunk_ids(self, chunk_ids: list[str]) -> dict[str, str]:
        """按分片 ID 读取正文，供父块三级缓存的 MySQL 回源使用。"""
        if not chunk_ids:
            return {}
        return await asyncio.to_thread(self._get_text_by_chunk_ids, chunk_ids)

    async def list_authorized_headers(
        self, document_id: int, document_version: int, accessible_by: list[str]
    ) -> list[AuthorizedKnowledgeSegment]:
        """读取同一文档版本分片的排序和父块关系，不加载正文以控制上下文扩展成本。"""
        if not accessible_by:
            return []
        return await asyncio.to_thread(
            self._list_authorized_headers,
            document_id,
            document_version,
            accessible_by,
        )

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password.get_secret_value(),
            database=self._settings.mysql_database,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=DictCursor,
        )

    def _replace_for_document(
        self,
        document_id: int,
        document_version: int,
        chunks: list[MarkdownChunk],
    ) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    # 重试必须幂等：先删除上次可能只写入一部分的分片，
                    # 再按新的顺序完整插入本次结果。
                    cursor.execute(
                        """
                        DELETE FROM knowledge_segment
                        WHERE document_id = %s AND document_version = %s
                        """,
                        (document_id, document_version),
                    )
                    if chunks:
                        cursor.executemany(
                            """
                            INSERT INTO knowledge_segment
                                (chunk_id, text, document_id, document_version, chunk_order, embedding_id,
                                 status, metadata, skip_embedding)
                            VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s)
                            """,
                            [
                                (
                                    chunk.chunk_id,
                                    chunk.text,
                                    document_id,
                                    document_version,
                                    index,
                                    "INIT",
                                    json.dumps(chunk.metadata, ensure_ascii=False),
                                    int(chunk.skip_embedding),
                                )
                                for index, chunk in enumerate(chunks)
                            ],
                        )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to save knowledge segments: {exc}"
            ) from exc

    def _list_pending_for_embedding(
        self, document_id: int, document_version: int, limit: int
    ) -> list[StoredKnowledgeSegment]:
        if limit <= 0:
            raise ValueError("Embedding page size must be positive")
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, chunk_id, text, metadata
                        FROM knowledge_segment
                        WHERE document_id = %s
                          AND document_version = %s
                          AND status = %s
                          AND embedding_id IS NULL
                          AND skip_embedding = 0
                        ORDER BY chunk_order, id
                        LIMIT %s
                        """,
                        (document_id, document_version, "INIT", limit),
                    )
                    result: list[StoredKnowledgeSegment] = []
                    for row in cursor.fetchall():
                        raw_metadata = row["metadata"]
                        try:
                            metadata = json.loads(raw_metadata) if raw_metadata else {}
                        except (TypeError, json.JSONDecodeError):
                            metadata = {}
                        if not isinstance(metadata, dict):
                            metadata = {}
                        result.append(
                            StoredKnowledgeSegment(
                                id=int(row["id"]),
                                chunk_id=str(row["chunk_id"]),
                                text=str(row["text"]),
                                metadata=metadata,
                            )
                        )
                    return result
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to list pending knowledge segments: {exc}"
            ) from exc

    def _mark_vectors_stored(self, rows: list[tuple[int, str]]) -> None:
        query = """
            UPDATE knowledge_segment
            SET embedding_id = %s, status = %s
            WHERE id = %s
        """
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        query,
                        [(embedding_id, "VECTOR_STORED", segment_id) for segment_id, embedding_id in rows],
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to mark knowledge segment vectors as stored: {exc}"
            ) from exc

    def _get_by_chunk_ids(self, chunk_ids: list[str]) -> list[StoredKnowledgeSegment]:
        placeholders = ", ".join(["%s"] * len(chunk_ids))
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT id, chunk_id, text, metadata FROM knowledge_segment WHERE chunk_id IN ({placeholders}) AND deleted = 0",
                        tuple(chunk_ids),
                    )
                    rows = {str(row["chunk_id"]): row for row in cursor.fetchall()}
            result: list[StoredKnowledgeSegment] = []
            for chunk_id in chunk_ids:
                row = rows.get(chunk_id)
                if row is None:
                    continue
                raw_metadata = row["metadata"]
                try:
                    metadata = json.loads(raw_metadata) if raw_metadata else {}
                except (TypeError, json.JSONDecodeError):
                    metadata = {}
                result.append(
                    StoredKnowledgeSegment(int(row["id"]), chunk_id, str(row["text"]), metadata if isinstance(metadata, dict) else {}))
            return result
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to read retrieved knowledge segments: {exc}"
            ) from exc

    def _get_authorized_by_chunk_ids(
        self, chunk_ids: list[str], accessible_by: list[str]
    ) -> list[AuthorizedKnowledgeSegment]:
        chunk_placeholders = ", ".join(["%s"] * len(chunk_ids))
        access_placeholders = ", ".join(["%s"] * len(accessible_by))
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT segment.chunk_id, segment.text, segment.metadata,
                               segment.document_id, segment.document_version, segment.chunk_order,
                               document.doc_title
                        FROM knowledge_segment segment
                        JOIN knowledge_document document
                          ON document.doc_id = segment.document_id
                        WHERE segment.chunk_id IN ({chunk_placeholders})
                          AND segment.deleted = 0
                          AND document.deleted = 0
                          AND document.status = %s
                          AND document.accessible_by IN ({access_placeholders})
                        """,
                        (*chunk_ids, "VECTOR_STORED", *accessible_by),
                    )
                    return [self._authorized_segment_from_row(row) for row in cursor.fetchall()]
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to reauthorize retrieved knowledge segments: {exc}"
            ) from exc

    def _get_authorized_refs_by_chunk_ids(
        self, chunk_ids: list[str], accessible_by: list[str]
    ) -> list[AuthorizedKnowledgeSegment]:
        """二次授权查询只取正文尾部，避免为父块回溯加载完整正文。"""
        chunk_placeholders = ", ".join(["%s"] * len(chunk_ids))
        access_placeholders = ", ".join(["%s"] * len(accessible_by))
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT segment.chunk_id, RIGHT(segment.text, 128) AS text, segment.metadata,
                               segment.document_id, segment.document_version, segment.chunk_order,
                               document.doc_title
                        FROM knowledge_segment segment
                        JOIN knowledge_document document
                          ON document.doc_id = segment.document_id
                        WHERE segment.chunk_id IN ({chunk_placeholders})
                          AND segment.deleted = 0
                          AND document.deleted = 0
                          AND document.status = %s
                          AND document.accessible_by IN ({access_placeholders})
                        """,
                        (*chunk_ids, "VECTOR_STORED", *accessible_by),
                    )
                    return [self._authorized_segment_from_row(row) for row in cursor.fetchall()]
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to reauthorize retrieved knowledge segments: {exc}"
            ) from exc

    def _get_text_by_chunk_ids(self, chunk_ids: list[str]) -> dict[str, str]:
        """只读取单表正文，用于缓存未命中时的 MySQL 回源。"""
        placeholders = ", ".join(["%s"] * len(chunk_ids))
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT chunk_id, text
                        FROM knowledge_segment
                        WHERE chunk_id IN ({placeholders}) AND deleted = 0
                        """,
                        tuple(chunk_ids),
                    )
                    return {
                        str(row["chunk_id"]): str(row["text"] or "")
                        for row in cursor.fetchall()
                    }
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to read knowledge segment text: {exc}"
            ) from exc

    def _list_authorized_headers(
        self,
        document_id: int,
        document_version: int,
        accessible_by: list[str],
    ) -> list[AuthorizedKnowledgeSegment]:
        access_placeholders = ", ".join(["%s"] * len(accessible_by))
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT segment.chunk_id, RIGHT(segment.text, 128) AS text, segment.metadata,
                               segment.document_id, segment.document_version, segment.chunk_order,
                               document.doc_title
                        FROM knowledge_segment segment
                        JOIN knowledge_document document
                          ON document.doc_id = segment.document_id
                        WHERE segment.document_id = %s
                          AND segment.document_version = %s
                          AND segment.deleted = 0
                          AND document.deleted = 0
                          AND document.status = %s
                          AND document.accessible_by IN ({access_placeholders})
                        ORDER BY segment.chunk_order, segment.id
                        """,
                        (document_id, document_version, "VECTOR_STORED", *accessible_by),
                    )
                    return [self._authorized_segment_from_row(row) for row in cursor.fetchall()]
        except pymysql.MySQLError as exc:
            raise KnowledgeSegmentRepositoryError(
                f"Unable to read authorized knowledge segment headers: {exc}"
            ) from exc

    @staticmethod
    def _authorized_segment_from_row(row: dict[str, Any]) -> AuthorizedKnowledgeSegment:
        """安全解析二次授权查询返回的分片行。"""
        raw_metadata = row.get("metadata")
        try:
            metadata = json.loads(raw_metadata) if raw_metadata else {}
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return AuthorizedKnowledgeSegment(
            chunk_id=str(row["chunk_id"]),
            text=str(row["text"]),
            metadata=metadata,
            document_id=int(row["document_id"]),
            document_version=int(row["document_version"]),
            chunk_order=int(row["chunk_order"]),
            document_title=str(row.get("doc_title") or ""),
        )
