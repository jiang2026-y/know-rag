"""员工访问持久化：更新 MySQL 员工权限，并保存幂等和审计记录。

定义仓储异常、IdempotencyReplay 和 StaffAccessRepository；仓储负责幂等声明、员工状态更新、审计写入与响应缓存。
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.staff_access import StaffAccessState, StaffAccessUpdate


class StaffAccessRepositoryError(RuntimeError):
    """员工访问、审计或幂等存储不可用时抛出。"""


class StaffNotFoundError(RuntimeError):
    """目标员工不存在时抛出。"""


class StaffAccessTargetNotOrdinaryError(PermissionError):
    """目标员工不是 job=staff 的普通员工时抛出。"""


class IdempotencyConflictError(RuntimeError):
    """同一幂等键被不同请求复用时抛出。"""


class IdempotencyInProgressError(RuntimeError):
    """同一幂等请求仍在处理时抛出。"""


@dataclass(frozen=True)
class IdempotencyReplay:
    """已完成幂等请求的安全响应缓存。"""

    payload: dict[str, Any]


class StaffAccessRepository:
    """将员工访问更新、审计记录和幂等状态持久化到 MySQL。"""

    _ENDPOINT = "PATCH:/api/admin/staff/{emp_id}/access"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def claim_idempotency(
        self, actor_emp_id: str, key: str, request_digest: str
    ) -> IdempotencyReplay | None:
        """声明幂等键；重复成功请求返回首次响应。"""
        return await asyncio.to_thread(
            self._claim_idempotency, actor_emp_id, key, request_digest
        )

    async def update_staff_access(
        self,
        actor_emp_id: str,
        target_emp_id: str,
        update: StaffAccessUpdate,
        trace_id: str,
    ) -> StaffAccessState:
        """在一个 MySQL 事务中锁定、更新员工并记录前后状态。"""
        return await asyncio.to_thread(
            self._update_staff_access, actor_emp_id, target_emp_id, update, trace_id
        )

    async def complete_idempotency(
        self, actor_emp_id: str, key: str, payload: dict[str, Any]
    ) -> None:
        """保存成功响应，供相同幂等请求直接重放。"""
        await asyncio.to_thread(self._complete_idempotency, actor_emp_id, key, payload)

    async def release_idempotency(self, actor_emp_id: str, key: str) -> None:
        """在未完成业务更新时释放声明，让调用方能够安全重试。"""
        await asyncio.to_thread(self._release_idempotency, actor_emp_id, key)

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password.get_secret_value(),
            database=self._settings.mysql_database,
            charset="utf8mb4",
            connect_timeout=self._settings.mysql_connect_timeout_seconds,
            autocommit=False,
        )

    def _claim_idempotency(
        self, actor_emp_id: str, key: str, request_digest: str
    ) -> IdempotencyReplay | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM api_idempotency_record WHERE expires_at <= UTC_TIMESTAMP()"
                    )
                    cursor.execute(
                        "DELETE FROM staff_access_audit WHERE expires_at <= UTC_TIMESTAMP()"
                    )
                    try:
                        cursor.execute(
                            """
                            INSERT INTO api_idempotency_record
                                (actor_emp_id, endpoint, idempotency_key, request_digest, state, expires_at)
                            VALUES (%s, %s, %s, %s, 'PROCESSING', DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR))
                            """,
                            (actor_emp_id, self._ENDPOINT, key, request_digest),
                        )
                    except pymysql.IntegrityError:
                        cursor.execute(
                            """
                            SELECT request_digest, state, response_payload
                            FROM api_idempotency_record
                            WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s
                            FOR UPDATE
                            """,
                            (actor_emp_id, self._ENDPOINT, key),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise StaffAccessRepositoryError("Idempotency record is unavailable")
                        existing_digest, state, response_payload = row
                        if existing_digest != request_digest:
                            raise IdempotencyConflictError("Idempotency key was used by a different request")
                        if state != "SUCCEEDED" or response_payload is None:
                            raise IdempotencyInProgressError("Idempotency request is still processing")
                        connection.commit()
                        return IdempotencyReplay(payload=json.loads(response_payload))
                connection.commit()
                return None
        except (
            IdempotencyConflictError,
            IdempotencyInProgressError,
            StaffAccessRepositoryError,
        ):
            raise
        except (pymysql.MySQLError, json.JSONDecodeError) as exc:
            raise StaffAccessRepositoryError("Unable to claim idempotency key") from exc

    def _update_staff_access(
        self,
        actor_emp_id: str,
        target_emp_id: str,
        update: StaffAccessUpdate,
        trace_id: str,
    ) -> StaffAccessState:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT emp_id, status, job, dept_id
                        FROM staff_info WHERE emp_id = %s FOR UPDATE
                        """,
                        (target_emp_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise StaffNotFoundError("Target employee was not found")
                    before = self._state_from_row(row)
                    if before.job != "staff":
                        raise StaffAccessTargetNotOrdinaryError(
                            "Only ordinary staff members can be modified"
                        )
                    assignments: list[str] = []
                    parameters: list[object] = []
                    if update.status is not None:
                        assignments.append("status = %s")
                        parameters.append(update.status)
                    if update.dept_id is not None:
                        assignments.append("dept_id = %s")
                        parameters.append(update.dept_id)
                    if not assignments:
                        raise ValueError("At least one staff access field is required")
                    parameters.append(target_emp_id)
                    cursor.execute(
                        f"UPDATE staff_info SET {', '.join(assignments)} WHERE emp_id = %s",
                        tuple(parameters),
                    )
                    cursor.execute(
                        "SELECT emp_id, status, job, dept_id FROM staff_info WHERE emp_id = %s",
                        (target_emp_id,),
                    )
                    after = self._state_from_row(cursor.fetchone())
                    cursor.execute(
                        """
                        INSERT INTO staff_access_audit
                            (actor_emp_id, target_emp_id, before_state, after_state, trace_id, expires_at)
                        VALUES (%s, %s, %s, %s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 6 MONTH))
                        """,
                        (
                            actor_emp_id,
                            target_emp_id,
                            json.dumps(before.__dict__, separators=(",", ":")),
                            json.dumps(after.__dict__, separators=(",", ":")),
                            trace_id,
                        ),
                    )
                connection.commit()
                return after
        except (StaffNotFoundError, StaffAccessTargetNotOrdinaryError, ValueError):
            raise
        except pymysql.MySQLError as exc:
            raise StaffAccessRepositoryError("Unable to update staff access") from exc

    def _complete_idempotency(
        self, actor_emp_id: str, key: str, payload: dict[str, Any]
    ) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE api_idempotency_record
                        SET state = 'SUCCEEDED', response_payload = %s
                        WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s
                        """,
                        (
                            json.dumps(payload, separators=(",", ":")),
                            actor_emp_id,
                            self._ENDPOINT,
                            key,
                        ),
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise StaffAccessRepositoryError("Unable to complete idempotency record") from exc

    def _release_idempotency(self, actor_emp_id: str, key: str) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM api_idempotency_record
                        WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s
                          AND state = 'PROCESSING'
                        """,
                        (actor_emp_id, self._ENDPOINT, key),
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise StaffAccessRepositoryError("Unable to release idempotency record") from exc

    @staticmethod
    def request_digest(target_emp_id: str, update: StaffAccessUpdate) -> str:
        """生成稳定请求摘要，防止同一幂等键承载不同变更。"""
        payload = {
            "target_emp_id": target_emp_id,
            "status": update.status,
            "dept_id": update.dept_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _state_from_row(row: tuple[object, ...] | None) -> StaffAccessState:
        """校验并转换 MySQL 员工访问行。"""
        if row is None:
            raise StaffAccessRepositoryError("Staff access row is unavailable")
        emp_id, status, job, dept_id = row
        if (
            not isinstance(emp_id, str)
            or status not in ("ON_JOB", "OFF_JOB")
            or not isinstance(job, str)
            or not job.strip()
            or (
                dept_id is not None
                and (isinstance(dept_id, bool) or not isinstance(dept_id, int) or dept_id < 1)
            )
        ):
            raise StaffAccessRepositoryError("Staff access data is invalid")
        return StaffAccessState(emp_id=emp_id, status=status, job=job.strip(), dept_id=dept_id)
