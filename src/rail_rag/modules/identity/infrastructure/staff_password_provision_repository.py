"""HR 初始密码仓储：管理已存在员工的空密码初始化、幂等响应与无敏感审计。

定义 StaffPasswordProvisionRepository、ProvisionedStaff、幂等与目标状态异常；仓储只允许为
在职且密码为空的员工保存已计算的 Argon2id 摘要，并在同一 MySQL 事务内完成审计与幂等响应。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.infrastructure.staff_access_repository import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyReplay,
)


class StaffPasswordProvisionRepositoryError(RuntimeError):
    """HR 初始密码、审计或幂等存储不可用时抛出。"""


class ProvisioningStaffNotFoundError(RuntimeError):
    """HR 请求指定的员工不存在时抛出。"""


class ProvisioningStaffNotEligibleError(RuntimeError):
    """员工不在职或已经拥有密码，不能再设置初始密码时抛出。"""


@dataclass(frozen=True)
class ProvisionedStaff:
    """内部开户完成后可安全返回给调用方的员工结果。"""

    emp_id: str


class StaffPasswordProvisionRepository:
    """持久化 HR 初始密码操作；不读取、返回或审计明文密码和密码摘要。"""

    _ENDPOINT = "POST:/internal/v1/hr/staff/{emp_id}/initial-password"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def claim_idempotency(
        self, service_actor: str, key: str, request_digest: str
    ) -> IdempotencyReplay | None:
        """声明内部开户幂等键；成功重试返回首次安全响应。"""
        return await asyncio.to_thread(
            self._claim_idempotency, service_actor, key, request_digest
        )

    async def ensure_eligible(self, emp_id: str) -> None:
        """确认员工仍存在、在职且没有任何已有密码。"""
        await asyncio.to_thread(self._ensure_eligible, emp_id)

    async def provision_and_complete(
        self,
        service_actor: str,
        service_name: str,
        emp_id: str,
        password_hash: str,
        idempotency_key: str,
        trace_id: str,
        revoked_sessions: int,
    ) -> ProvisionedStaff:
        """原子写入密码摘要、审计和幂等成功响应。"""
        return await asyncio.to_thread(
            self._provision_and_complete,
            service_actor,
            service_name,
            emp_id,
            password_hash,
            idempotency_key,
            trace_id,
            revoked_sessions,
        )

    async def release_idempotency(self, service_actor: str, key: str) -> None:
        """未完成时释放幂等声明，允许 HR 修正后重试。"""
        await asyncio.to_thread(self._release_idempotency, service_actor, key)

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
        self, service_actor: str, key: str, request_digest: str
    ) -> IdempotencyReplay | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM api_idempotency_record WHERE expires_at <= UTC_TIMESTAMP()"
                    )
                    cursor.execute(
                        "DELETE FROM staff_password_provision_audit WHERE expires_at <= UTC_TIMESTAMP()"
                    )
                    try:
                        cursor.execute(
                            """
                            INSERT INTO api_idempotency_record
                                (actor_emp_id, endpoint, idempotency_key, request_digest, state, expires_at)
                            VALUES (%s, %s, %s, %s, 'PROCESSING', DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR))
                            """,
                            (service_actor, self._ENDPOINT, key, request_digest),
                        )
                    except pymysql.IntegrityError:
                        cursor.execute(
                            """
                            SELECT request_digest, state, response_payload
                            FROM api_idempotency_record
                            WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s
                            FOR UPDATE
                            """,
                            (service_actor, self._ENDPOINT, key),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise StaffPasswordProvisionRepositoryError("Idempotency record is unavailable")
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
            StaffPasswordProvisionRepositoryError,
        ):
            raise
        except (pymysql.MySQLError, json.JSONDecodeError) as exc:
            raise StaffPasswordProvisionRepositoryError("Unable to claim provisioning idempotency key") from exc

    def _ensure_eligible(self, emp_id: str) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT status, password FROM staff_info WHERE emp_id = %s",
                        (emp_id,),
                    )
                    self._ensure_eligible_row(cursor.fetchone())
        except (ProvisioningStaffNotFoundError, ProvisioningStaffNotEligibleError):
            raise
        except pymysql.MySQLError as exc:
            raise StaffPasswordProvisionRepositoryError("Unable to validate provisioning employee") from exc

    def _provision_and_complete(
        self,
        service_actor: str,
        service_name: str,
        emp_id: str,
        password_hash: str,
        idempotency_key: str,
        trace_id: str,
        revoked_sessions: int,
    ) -> ProvisionedStaff:
        response_payload = {
            "emp_id": emp_id,
            "password_format": "ARGON2ID",
            "revoked_sessions": revoked_sessions,
        }
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT status, password FROM staff_info WHERE emp_id = %s FOR UPDATE",
                        (emp_id,),
                    )
                    self._ensure_eligible_row(cursor.fetchone())
                    cursor.execute(
                        "UPDATE staff_info SET password = %s WHERE emp_id = %s",
                        (password_hash, emp_id),
                    )
                    cursor.execute(
                        """
                        INSERT INTO staff_password_provision_audit
                            (service_name, target_emp_id, outcome, trace_id, expires_at)
                        VALUES (%s, %s, 'SUCCEEDED', %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 6 MONTH))
                        """,
                        (service_name, emp_id, trace_id),
                    )
                    affected = cursor.execute(
                        """
                        UPDATE api_idempotency_record
                        SET state = 'SUCCEEDED', response_payload = %s
                        WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s
                          AND state = 'PROCESSING'
                        """,
                        (
                            json.dumps(response_payload, separators=(",", ":")),
                            service_actor,
                            self._ENDPOINT,
                            idempotency_key,
                        ),
                    )
                    if affected != 1:
                        raise StaffPasswordProvisionRepositoryError("Idempotency record is unavailable")
                connection.commit()
                return ProvisionedStaff(emp_id=emp_id)
        except (
            ProvisioningStaffNotFoundError,
            ProvisioningStaffNotEligibleError,
            StaffPasswordProvisionRepositoryError,
        ):
            raise
        except pymysql.MySQLError as exc:
            raise StaffPasswordProvisionRepositoryError("Unable to provision initial password") from exc

    def _release_idempotency(self, service_actor: str, key: str) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM api_idempotency_record
                        WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s
                          AND state = 'PROCESSING'
                        """,
                        (service_actor, self._ENDPOINT, key),
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise StaffPasswordProvisionRepositoryError("Unable to release provisioning idempotency key") from exc

    @staticmethod
    def _ensure_eligible_row(row: tuple[object, ...] | None) -> None:
        """验证员工存在、在职且密码为 NULL 或空字符串。"""
        if row is None:
            raise ProvisioningStaffNotFoundError("Staff member was not found")
        status, password = row
        if status != "ON_JOB" or (isinstance(password, str) and password.strip()):
            raise ProvisioningStaffNotEligibleError(
                "Only active staff members without a password can be provisioned"
            )
        if password is not None and not isinstance(password, str):
            raise StaffPasswordProvisionRepositoryError("Staff password state is invalid")
