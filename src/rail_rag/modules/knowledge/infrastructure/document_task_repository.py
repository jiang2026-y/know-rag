import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from rail_rag.config.settings import Settings


class DocumentTaskRepositoryError(RuntimeError):
    """无法读取或更新持久化文档任务状态时抛出。"""


@dataclass(frozen=True)
class StoredDocumentTask:
    task_id: str
    document_id: int
    version_id: int
    original_filename: str
    state: str
    document_status: str
    source_object_name: str | None
    markdown_object_name: str | None
    image_object_names: list[str]
    error_message: str | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime


class DocumentTaskRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def create(
        self,
        task_id: str,
        document_id: int,
        version_id: int,
        filename: str,
        source_object_name: str,
    ) -> StoredDocumentTask:
        await asyncio.to_thread(
            self._execute,
            """
            INSERT INTO document_processing_task
                (task_id, document_id, version_id, original_filename, state, document_status, source_object_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                document_id,
                version_id,
                filename,
                "queued",
                "UPLOADED",
                source_object_name,
            ),
        )
        return await self.get(task_id)

    async def get(self, task_id: str) -> StoredDocumentTask:
        row = await asyncio.to_thread(
            self._fetch_one,
            "SELECT * FROM document_processing_task WHERE task_id = %s",
            (task_id,),
        )
        if row is None:
            raise KeyError(task_id)
        return self._to_task(row)

    async def list_queued(self, limit: int = 100) -> list[StoredDocumentTask]:
        """读取有限数量的待投递任务，供恢复调度器在 Broker 恢复后补发。"""
        rows = await asyncio.to_thread(
            self._fetch_all,
            """
            SELECT * FROM document_processing_task
            WHERE state = %s
            ORDER BY created_at ASC
            LIMIT %s
            """,
            ("queued", limit),
        )
        return [self._to_task(row) for row in rows]

    async def start(self, task_id: str) -> StoredDocumentTask | None:
        affected = await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s, error_message = NULL,
                attempt_count = attempt_count + 1, started_at = UTC_TIMESTAMP()
            WHERE task_id = %s AND state = %s
            """,
            ("processing", "CONVERTING", task_id, "queued"),
        )
        return await self.get(task_id) if affected == 1 else None

    async def complete(
        self,
        task_id: str,
        markdown_object_name: str,
        image_object_names: list[str],
        document_status: str = "VECTOR_STORED",
    ) -> None:
        await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s, markdown_object_name = %s,
                image_object_names = %s,
                error_message = NULL, completed_at = UTC_TIMESTAMP()
            WHERE task_id = %s
            """,
            (
                "completed",
                document_status,
                markdown_object_name,
                json.dumps(image_object_names),
                task_id,
            ),
        )

    async def fail(self, task_id: str, error_message: str) -> None:
        await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s, error_message = %s,
                completed_at = UTC_TIMESTAMP()
            WHERE task_id = %s
            """,
            ("failed", "FAILED", error_message[:4000], task_id),
        )

    async def requeue(self, task_id: str) -> StoredDocumentTask | None:
        affected = await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s, error_message = NULL,
                started_at = NULL, completed_at = NULL
            WHERE task_id = %s AND state = %s
            """,
            ("queued", "UPLOADED", task_id, "failed"),
        )
        return await self.get(task_id) if affected == 1 else None

    async def requeue_after_failure(
        self, task_id: str, error_message: str, max_auto_retries: int
    ) -> StoredDocumentTask | None:
        """在尚有自动重试次数时，以原子状态转换将处理中任务重新入队。"""
        affected = await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s, error_message = %s,
                started_at = NULL, completed_at = NULL
            WHERE task_id = %s AND state = %s
              AND attempt_count <= %s
            """,
            (
                "queued",
                "UPLOADED",
                error_message[:4000],
                task_id,
                "processing",
                max_auto_retries,
            ),
        )
        return await self.get(task_id) if affected == 1 else None

    async def dead_letter(self, task_id: str, error_message: str) -> StoredDocumentTask | None:
        """将耗尽自动重试次数的处理中任务标记为死信，等待 leader 人工处理。"""
        affected = await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s, error_message = %s,
                completed_at = UTC_TIMESTAMP()
            WHERE task_id = %s AND state = %s
            """,
            ("dead_lettered", "FAILED", error_message[:4000], task_id, "processing"),
        )
        return await self.get(task_id) if affected == 1 else None

    async def replay_dead_lettered(self, task_id: str) -> StoredDocumentTask | None:
        """把已进入死信队列的文档任务恢复为可由 leader 重放的排队状态。"""
        affected = await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s, error_message = NULL,
                started_at = NULL, completed_at = NULL
            WHERE task_id = %s AND state = %s
            """,
            ("queued", "UPLOADED", task_id, "dead_lettered"),
        )
        return await self.get(task_id) if affected == 1 else None

    async def cancel(self, task_id: str) -> StoredDocumentTask | None:
        """原子取消仍在排队或处理中的任务。"""
        affected = await asyncio.to_thread(
            self._execute,
            "UPDATE document_processing_task SET state = %s, document_status = %s, error_message = %s WHERE task_id = %s AND state IN (%s, %s)",
            ("cancelled", "CANCELLED", "Cancelled by authorized operator", task_id, "queued", "processing"),
        )
        return await self.get(task_id) if affected == 1 else None

    async def mark_interrupted_tasks_failed(self) -> list[int]:
        rows = await asyncio.to_thread(
            self._fetch_all,
            "SELECT document_id FROM document_processing_task WHERE state = %s",
            ("processing",),
        )
        if not rows:
            return []
        await asyncio.to_thread(
            self._execute,
            """
            UPDATE document_processing_task
            SET state = %s, document_status = %s,
                error_message = %s, completed_at = UTC_TIMESTAMP()
            WHERE state = %s
            """,
            ("failed", "FAILED", "Processing interrupted by application restart", "processing"),
        )
        return [row["document_id"] for row in rows]

    async def get_image_object_names(self, document_id: int) -> list[str]:
        """读取某文档最近一次处理结果中的图片对象名，用于受控下载校验。"""
        row = await asyncio.to_thread(
            self._fetch_one,
            """
            SELECT image_object_names FROM document_processing_task
            WHERE document_id = %s AND state = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (document_id, "completed"),
        )
        if row is None:
            return []
        return self._image_object_names(row.get("image_object_names"))

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password.get_secret_value(),
            database=self._settings.mysql_database,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=DictCursor,
            connect_timeout=self._settings.mysql_connect_timeout_seconds,
        )

    def _execute(self, query: str, parameters: tuple[Any, ...]) -> int:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    return cursor.execute(query, parameters)
        except pymysql.MySQLError as exc:
            raise DocumentTaskRepositoryError(f"Unable to update document task: {exc}") from exc

    def _fetch_one(self, query: str, parameters: tuple[Any, ...]) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, parameters)
                    return cursor.fetchone()
        except pymysql.MySQLError as exc:
            raise DocumentTaskRepositoryError(f"Unable to read document task: {exc}") from exc

    def _fetch_all(self, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, parameters)
                    return list(cursor.fetchall())
        except pymysql.MySQLError as exc:
            raise DocumentTaskRepositoryError(f"Unable to read document tasks: {exc}") from exc

    @staticmethod
    def _to_task(row: dict[str, Any]) -> StoredDocumentTask:
        image_object_names = DocumentTaskRepository._image_object_names(
            row.get("image_object_names")
        )
        return StoredDocumentTask(
            task_id=row["task_id"],
            document_id=row["document_id"],
            version_id=row["version_id"],
            original_filename=row["original_filename"],
            state=row["state"],
            document_status=row["document_status"],
            source_object_name=row["source_object_name"],
            markdown_object_name=row["markdown_object_name"],
            image_object_names=image_object_names,
            error_message=row["error_message"],
            attempt_count=row["attempt_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _image_object_names(raw_images: object) -> list[str]:
        """将任务表中的 JSON 图片列表安全转换为字符串列表。"""
        try:
            image_object_names = json.loads(raw_images) if raw_images else []
        except (TypeError, json.JSONDecodeError):
            image_object_names = []
        if not isinstance(image_object_names, list) or not all(
            isinstance(name, str) for name in image_object_names
        ):
            return []
        return image_object_names
