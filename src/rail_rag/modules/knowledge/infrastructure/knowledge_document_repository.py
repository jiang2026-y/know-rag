import asyncio
from dataclasses import dataclass
from datetime import datetime
import json

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.domain.document_access import DocumentAccessLabel


class KnowledgeDocumentRepositoryError(RuntimeError):
    """知识文档元数据存储不可用时抛出。"""


class KnowledgeDocumentReviewStateError(ValueError):
    """文档未处于可审核状态或审核标签不一致时抛出。"""


@dataclass(frozen=True)
class StoredKnowledgeDocument:
    """向量化流程所需的最小文档状态。"""

    document_id: int
    status: str
    version_id: int | None = None
    accessible_by: DocumentAccessLabel | None = None
    upload_user: str | None = None
    converted_url: str | None = None
    lifecycle_status: str = "ENABLED"


@dataclass(frozen=True)
class ListedKnowledgeDocument:
    """审核列表展示所需的最小、非敏感文档元数据。"""

    document_id: int
    title: str
    status: str
    accessible_by: DocumentAccessLabel | None
    upload_user: str | None
    updated_at: datetime


class KnowledgeDocumentRepository:
    """面向``knowledge_document`` 表的最小 MySQL 持久化实现。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def create_uploaded_document(
        self,
        title: str,
        upload_user: str,
        *,
        split_param: dict[str, object] | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._create_uploaded_document, title, upload_user, split_param
        )

    async def get_version_split_param(
        self, document_id: int, version_id: int
    ) -> dict[str, object] | None:
        """读取版本级切片参数；未记录时返回 None，表示使用默认切片策略。"""
        return await asyncio.to_thread(
            self._get_version_split_param, document_id, version_id
        )

    async def update_status(self, document_id: int, status: str) -> None:
        await asyncio.to_thread(self._update_status, document_id, status)

    async def get(self, document_id: int) -> StoredKnowledgeDocument | None:
        return await asyncio.to_thread(self._get, document_id)

    async def list_by_statuses(
        self, statuses: tuple[str, ...], *, upload_user: str | None = None, limit: int = 50
    ) -> list[ListedKnowledgeDocument]:
        """按服务层确定的状态和上传人范围返回有限条文档元数据。"""
        return await asyncio.to_thread(
            self._list_by_statuses, statuses, upload_user=upload_user, limit=limit
        )

    async def get_current_version_id(self, document_id: int) -> int:
        return await asyncio.to_thread(self._get_current_version_id, document_id)

    async def complete_document(
        self, document_id: int, source_url: str, converted_url: str
    ) -> None:
        await asyncio.to_thread(
            self._complete_document, document_id, source_url, converted_url
        )

    async def approve_for_vectorization(
        self, document_id: int, accessible_by: DocumentAccessLabel
    ) -> StoredKnowledgeDocument:
        """保存领导批准的标签，并将已解析分片准备为可向量化状态。"""
        return await asyncio.to_thread(
            self._approve_for_vectorization, document_id, accessible_by
        )

    async def reject_review(self, document_id: int) -> StoredKnowledgeDocument:
        """将待审核文档标记为驳回，禁止其进入检索流程。"""
        return await asyncio.to_thread(self._reject_review, document_id)

    async def update_source_url(self, document_id: int, source_url: str) -> None:
        await asyncio.to_thread(self._update_source_url, document_id, source_url)

    async def check_connection(self) -> None:
        await asyncio.to_thread(self._check_connection)

    async def update_lifecycle_status(self, document_id: int, lifecycle_status: str) -> None:
        """更新文档生命周期；处理状态和生命周期彼此独立。"""
        await asyncio.to_thread(self._update_lifecycle_status, document_id, lifecycle_status)

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password.get_secret_value(),
            database=self._settings.mysql_database,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=self._settings.mysql_connect_timeout_seconds,
        )

    def _create_uploaded_document(
        self,
        title: str,
        upload_user: str,
        split_param: dict[str, object] | None = None,
    ) -> int:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO knowledge_document
                            (doc_title, status, accessible_by)
                        VALUES (%s, %s, %s)
                        """,
                        (title, "UPLOADED", None),
                    )
                    document_id = cursor.lastrowid
                    if not isinstance(document_id, int):
                        raise KnowledgeDocumentRepositoryError(
                            "MySQL did not return a knowledge document ID"
                        )
                    cursor.execute(
                        """
                        INSERT INTO knowledge_document_version
                            (doc_id, version, status, upload_user)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (document_id, "1.0.0", "UPLOADED", upload_user),
                    )
                    version_id = cursor.lastrowid
                    if not isinstance(version_id, int):
                        raise KnowledgeDocumentRepositoryError(
                            "MySQL did not return a knowledge document version ID"
                        )
                    if split_param is not None:
                        # 仅在显式传入切片参数时写入该列，使未执行迁移的环境仍可上传。
                        cursor.execute(
                            """
                            UPDATE knowledge_document_version
                            SET split_param = %s
                            WHERE version_id = %s
                            """,
                            (
                                json.dumps(split_param, ensure_ascii=False),
                                version_id,
                            ),
                        )
                    cursor.execute(
                        """
                        UPDATE knowledge_document
                        SET current_version_id = %s
                        WHERE doc_id = %s
                        """,
                        (version_id, document_id),
                    )
                connection.commit()
                return document_id
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to create knowledge document metadata: {exc}"
            ) from exc

    def _get_version_split_param(
        self, document_id: int, version_id: int
    ) -> dict[str, object] | None:
        """读取版本级切片参数 JSON，缺失或损坏时按默认参数处理。"""
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT split_param
                        FROM knowledge_document_version
                        WHERE version_id = %s AND doc_id = %s AND deleted = 0
                        """,
                        (version_id, document_id),
                    )
                    row = cursor.fetchone()
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to read knowledge document split parameters: {exc}"
            ) from exc
        if not row or row[0] is None:
            return None
        raw = row[0]
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _update_status(self, document_id: int, status: str) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE knowledge_document
                        SET status = %s
                        WHERE doc_id = %s
                        """,
                        (status.upper(), document_id),
                    )
                    if cursor.rowcount != 1:
                        raise KnowledgeDocumentRepositoryError(
                            f"Knowledge document not found: {document_id}"
                        )
                    cursor.execute(
                        """
                        UPDATE knowledge_document_version version
                        JOIN knowledge_document document
                          ON document.current_version_id = version.version_id
                        SET version.status = %s
                        WHERE document.doc_id = %s
                        """,
                        (status.upper(), document_id),
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to update knowledge document status: {exc}"
            ) from exc

    def _get(self, document_id: int) -> StoredKnowledgeDocument | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT document.doc_id, document.status, document.current_version_id,
                               document.accessible_by, version.upload_user,
                               version.converted_doc_url, document.lifecycle_status
                        FROM knowledge_document document
                        LEFT JOIN knowledge_document_version version
                          ON version.version_id = document.current_version_id
                        WHERE document.doc_id = %s AND document.deleted = 0
                        """,
                        (document_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    return self._stored_document_from_row(row)
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to read knowledge document metadata: {exc}"
            ) from exc

    def _list_by_statuses(
        self, statuses: tuple[str, ...], *, upload_user: str | None, limit: int
    ) -> list[ListedKnowledgeDocument]:
        if not statuses or limit < 1 or limit > 100:
            raise ValueError("Document listing parameters are invalid")
        placeholders = ", ".join(["%s"] * len(statuses))
        where_upload_user = ""
        parameters: list[object] = [*statuses]
        if upload_user is not None:
            where_upload_user = "AND version.upload_user = %s"
            parameters.append(upload_user)
        parameters.append(limit)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT document.doc_id, document.doc_title, document.status,
                               document.accessible_by, version.upload_user, document.updated_at
                        FROM knowledge_document document
                        JOIN knowledge_document_version version
                          ON version.version_id = document.current_version_id
                        WHERE document.deleted = 0
                          AND document.status IN ({placeholders})
                          {where_upload_user}
                        ORDER BY document.updated_at DESC, document.doc_id DESC
                        LIMIT %s
                        """,
                        tuple(parameters),
                    )
                    return [self._listed_document_from_row(row) for row in cursor.fetchall()]
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to list knowledge document metadata: {exc}"
            ) from exc

    def _complete_document(
        self, document_id: int, source_url: str, converted_url: str
    ) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE knowledge_document
                        SET status = %s
                        WHERE doc_id = %s
                        """,
                        ("PENDING_REVIEW", document_id),
                    )
                    if cursor.rowcount != 1:
                        raise KnowledgeDocumentRepositoryError(
                            f"Knowledge document not found: {document_id}"
                        )
                    cursor.execute(
                        """
                        UPDATE knowledge_document_version version
                        JOIN knowledge_document document
                          ON document.current_version_id = version.version_id
                        SET version.status = %s, version.doc_url = %s,
                            version.converted_doc_url = %s
                        WHERE document.doc_id = %s
                        """,
                        ("PENDING_REVIEW", source_url, converted_url, document_id),
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to complete knowledge document metadata: {exc}"
            ) from exc

    def _update_source_url(self, document_id: int, source_url: str) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE knowledge_document_version version
                        JOIN knowledge_document document
                          ON document.current_version_id = version.version_id
                        SET version.doc_url = %s
                        WHERE document.doc_id = %s
                        """,
                        (source_url, document_id),
                    )
                    if cursor.rowcount != 1:
                        raise KnowledgeDocumentRepositoryError(
                            f"Knowledge document not found: {document_id}"
                        )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to update knowledge document source URL: {exc}"
            ) from exc

    def _approve_for_vectorization(
        self, document_id: int, accessible_by: DocumentAccessLabel
    ) -> StoredKnowledgeDocument:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT document.doc_id, document.status, document.current_version_id,
                               document.accessible_by, version.upload_user,
                               version.converted_doc_url
                        FROM knowledge_document document
                        LEFT JOIN knowledge_document_version version
                          ON version.version_id = document.current_version_id
                        WHERE document.doc_id = %s AND document.deleted = 0
                        FOR UPDATE
                        """,
                        (document_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise KnowledgeDocumentReviewStateError("Knowledge document was not found")
                    document = self._stored_document_from_row(row)
                    if document.status not in ("PENDING_REVIEW", "CHUNKED"):
                        raise KnowledgeDocumentReviewStateError(
                            "Knowledge document is not ready for approval"
                        )
                    if document.status == "CHUNKED" and document.accessible_by != accessible_by:
                        raise KnowledgeDocumentReviewStateError(
                            "Approved document label cannot be changed during vectorization"
                        )
                    if document.version_id is None:
                        raise KnowledgeDocumentReviewStateError(
                            "Knowledge document has no active version"
                        )
                    cursor.execute(
                        """
                        UPDATE knowledge_document
                        SET accessible_by = %s, status = %s
                        WHERE doc_id = %s
                        """,
                        (accessible_by, "CHUNKED", document_id),
                    )
                    cursor.execute(
                        """
                        UPDATE knowledge_document_version
                        SET status = %s
                        WHERE version_id = %s
                        """,
                        ("CHUNKED", document.version_id),
                    )
                    cursor.execute(
                        """
                        UPDATE knowledge_segment
                        SET metadata = JSON_SET(
                            COALESCE(metadata, JSON_OBJECT()), '$.accessibleBy', %s
                        )
                        WHERE document_id = %s AND document_version = %s AND deleted = 0
                        """,
                        (accessible_by, document_id, document.version_id),
                    )
                connection.commit()
                return StoredKnowledgeDocument(
                    document_id=document.document_id,
                    status="CHUNKED",
                    version_id=document.version_id,
                    accessible_by=accessible_by,
                    upload_user=document.upload_user,
                    converted_url=document.converted_url,
                )
        except KnowledgeDocumentReviewStateError:
            raise
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to approve knowledge document: {exc}"
            ) from exc

    def _reject_review(self, document_id: int) -> StoredKnowledgeDocument:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT document.doc_id, document.status, document.current_version_id,
                               document.accessible_by, version.upload_user,
                               version.converted_doc_url
                        FROM knowledge_document document
                        LEFT JOIN knowledge_document_version version
                          ON version.version_id = document.current_version_id
                        WHERE document.doc_id = %s AND document.deleted = 0
                        FOR UPDATE
                        """,
                        (document_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise KnowledgeDocumentReviewStateError("Knowledge document was not found")
                    document = self._stored_document_from_row(row)
                    if document.status != "PENDING_REVIEW":
                        raise KnowledgeDocumentReviewStateError(
                            "Only pending-review documents can be rejected"
                        )
                    cursor.execute(
                        "UPDATE knowledge_document SET status = %s WHERE doc_id = %s",
                        ("REJECTED", document_id),
                    )
                    if document.version_id is not None:
                        cursor.execute(
                            "UPDATE knowledge_document_version SET status = %s WHERE version_id = %s",
                            ("REJECTED", document.version_id),
                        )
                connection.commit()
                return StoredKnowledgeDocument(
                    document_id=document.document_id,
                    status="REJECTED",
                    version_id=document.version_id,
                    accessible_by=None,
                    upload_user=document.upload_user,
                    converted_url=document.converted_url,
                )
        except KnowledgeDocumentReviewStateError:
            raise
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to reject knowledge document: {exc}"
            ) from exc

    def _check_connection(self) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to connect to MySQL know_engine: {exc}"
            ) from exc

    def _get_current_version_id(self, document_id: int) -> int:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT current_version_id
                        FROM knowledge_document
                        WHERE doc_id = %s AND deleted = 0
                        """,
                        (document_id,),
                    )
                    row = cursor.fetchone()
                    if row is None or row[0] is None:
                        raise KnowledgeDocumentRepositoryError(
                            f"Knowledge document version not found: {document_id}"
                        )
                    return int(row[0])
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError(
                f"Unable to read knowledge document version: {exc}"
            ) from exc

    @staticmethod
    def _stored_document_from_row(row: tuple[object, ...]) -> StoredKnowledgeDocument:
        """校验并转换包含审核、上传人和转换结果的文档查询行。"""
        document_id, status, version_id, accessible_by, upload_user, converted_url, *rest = row
        if not isinstance(document_id, int) or not isinstance(status, str):
            raise KnowledgeDocumentRepositoryError("Knowledge document data is invalid")
        if accessible_by is not None and accessible_by not in {"VISITOR", "STAFF", "LEADER"}:
            raise KnowledgeDocumentRepositoryError("Knowledge document access label is invalid")
        return StoredKnowledgeDocument(
            document_id=document_id,
            status=status,
            version_id=int(version_id) if version_id is not None else None,
            accessible_by=accessible_by,
            upload_user=upload_user if isinstance(upload_user, str) else None,
            converted_url=converted_url if isinstance(converted_url, str) else None,
            lifecycle_status=rest[0] if rest and rest[0] in {"ENABLED", "DISABLED", "ARCHIVED", "DELETED"} else "ENABLED",
        )

    def _update_lifecycle_status(self, document_id: int, lifecycle_status: str) -> None:
        if lifecycle_status not in {"ENABLED", "DISABLED", "ARCHIVED", "DELETED"}:
            raise ValueError("Invalid lifecycle status")
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE knowledge_document SET lifecycle_status = %s, deleted = %s WHERE doc_id = %s AND deleted = 0", (lifecycle_status, 1 if lifecycle_status == "DELETED" else 0, document_id))
                    if cursor.rowcount != 1:
                        raise KnowledgeDocumentRepositoryError("Knowledge document was not found")
                connection.commit()
        except pymysql.MySQLError as exc:
            raise KnowledgeDocumentRepositoryError("Unable to update document lifecycle") from exc

    @staticmethod
    def _listed_document_from_row(row: tuple[object, ...]) -> ListedKnowledgeDocument:
        """校验并转换审核列表所需的文档查询行。"""
        document_id, title, status, accessible_by, upload_user, updated_at = row
        if (
            not isinstance(document_id, int)
            or not isinstance(title, str)
            or not isinstance(status, str)
            or not isinstance(updated_at, datetime)
        ):
            raise KnowledgeDocumentRepositoryError("Knowledge document list data is invalid")
        if accessible_by is not None and accessible_by not in {"VISITOR", "STAFF", "LEADER"}:
            raise KnowledgeDocumentRepositoryError("Knowledge document access label is invalid")
        return ListedKnowledgeDocument(
            document_id=document_id,
            title=title,
            status=status,
            accessible_by=accessible_by,
            upload_user=upload_user if isinstance(upload_user, str) else None,
            updated_at=updated_at,
        )
