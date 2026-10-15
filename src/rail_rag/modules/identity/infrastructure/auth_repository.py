"""认证账号仓储：读取客户与员工账号，并在服务端完成 Argon2id/旧 PBKDF2 密码校验。

定义 AuthenticationError、AuthenticationRepository 和 AuthenticatedAccount；
仓储负责查询可登录账号，返回可创建会话的可信身份主体。
"""

import asyncio
from dataclasses import dataclass

import pymysql

from rail_rag.modules.identity.domain.principal import PrincipalType, SessionPrincipal
from rail_rag.modules.identity.infrastructure.password_hasher import PasswordHasher
from rail_rag.config.settings import Settings


class AuthenticationError(RuntimeError):
    """认证数据源不可用时抛出。"""


@dataclass(frozen=True)
class AuthenticatedAccount:
    """已通过账号密码验证、可创建会话的身份信息。"""

    principal: SessionPrincipal
    stored_password_hash: str
    needs_password_rehash: bool


class AuthenticationRepository:
    """从 MySQL 的客户和员工表校验 Argon2id，并在成功登录后升级旧摘要。"""

    def __init__(self, settings: Settings, password_hasher: PasswordHasher) -> None:
        self._settings = settings
        self._password_hasher = password_hasher

    async def authenticate_customer(
        self, phone: str, password: str
    ) -> AuthenticatedAccount | None:
        """验证状态为 ACTIVE 的客户账号。"""
        return await asyncio.to_thread(
            self._authenticate_customer, phone.strip(), password
        )

    async def authenticate_staff(
        self, emp_id: str, password: str
    ) -> AuthenticatedAccount | None:
        """验证状态为 ON_JOB 的员工账号。"""
        return await asyncio.to_thread(self._authenticate_staff, emp_id.strip(), password)

    async def authenticate_customer_by_id(
        self, user_id: str, password: str
    ) -> AuthenticatedAccount | None:
        """按当前可信顾客 ID 校验修改密码时提交的旧密码。"""
        return await asyncio.to_thread(
            self._authenticate_customer_by_id, user_id.strip(), password
        )

    async def update_customer_password(
        self, account: AuthenticatedAccount, new_password: str
    ) -> None:
        """以旧摘要为条件写入顾客新的 Argon2id 摘要，避免覆盖并发修改。"""
        if account.principal.principal_type != "CUSTOMER":
            raise AuthenticationError("Only customer passwords can be changed here")
        await asyncio.to_thread(self._update_customer_password, account, new_password)

    async def get_active_staff_principal(self, emp_id: str) -> SessionPrincipal | None:
        """读取员工当前在职状态、岗位和部门，用于每次私有请求的实时权限校验。"""
        return await asyncio.to_thread(self._get_active_staff_principal, emp_id.strip())

    async def upgrade_password_hash(
        self, account: AuthenticatedAccount, password: str
    ) -> None:
        """将已成功验证的旧摘要原子替换为当前 Argon2id 摘要。"""
        if not account.needs_password_rehash:
            return
        await asyncio.to_thread(self._upgrade_password_hash, account, password)

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password.get_secret_value(),
            database=self._settings.mysql_database,
            charset="utf8mb4",
            connect_timeout=self._settings.mysql_connect_timeout_seconds,
        )

    def _authenticate_customer(
        self, phone: str, password: str
    ) -> AuthenticatedAccount | None:
        if not phone:
            return None
        row = self._fetch_one(
            """
            SELECT id, password
            FROM user_info
            WHERE phone = %s AND status = %s
            """,
            (phone, "ACTIVE"),
        )
        return self._account_from_row(row, password, "CUSTOMER")

    def _authenticate_staff(
        self, emp_id: str, password: str
    ) -> AuthenticatedAccount | None:
        if not emp_id:
            return None
        row = self._fetch_one(
            """
            SELECT emp_id, password, job, dept_id
            FROM staff_info
            WHERE emp_id = %s AND status = %s
            """,
            (emp_id, "ON_JOB"),
        )
        if row is None:
            return None
        emp_id_value, stored_hash, job, dept_id = row
        return self._account_from_row(
            (emp_id_value, stored_hash), password, "STAFF", job=job, dept_id=dept_id
        )

    def _authenticate_customer_by_id(
        self, user_id: str, password: str
    ) -> AuthenticatedAccount | None:
        if not user_id:
            return None
        row = self._fetch_one(
            """
            SELECT id, password
            FROM user_info
            WHERE id = %s AND status = %s
            """,
            (user_id, "ACTIVE"),
        )
        return self._account_from_row(row, password, "CUSTOMER")

    def _get_active_staff_principal(self, emp_id: str) -> SessionPrincipal | None:
        if not emp_id:
            return None
        row = self._fetch_one(
            """
            SELECT emp_id, job, dept_id
            FROM staff_info
            WHERE emp_id = %s AND status = %s
            """,
            (emp_id, "ON_JOB"),
        )
        if row is None:
            return None
        return self._staff_principal(*row)

    def _fetch_one(self, query: str, parameters: tuple[str, ...]) -> tuple[object, ...] | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, parameters)
                    return cursor.fetchone()
        except pymysql.MySQLError as exc:
            raise AuthenticationError(f"Unable to read authentication account: {exc}") from exc

    def _upgrade_password_hash(self, account: AuthenticatedAccount, password: str) -> None:
        new_hash = self._password_hasher.hash(password)
        if account.principal.principal_type == "CUSTOMER":
            query = "UPDATE user_info SET password = %s WHERE id = %s AND password = %s"
        else:
            query = "UPDATE staff_info SET password = %s WHERE emp_id = %s AND password = %s"
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    affected = cursor.execute(
                        query,
                        (new_hash, account.principal.principal_id, account.stored_password_hash),
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise AuthenticationError("Unable to upgrade password hash") from exc
        if affected != 1:
            raise AuthenticationError("Password hash changed during authentication")

    def _update_customer_password(
        self, account: AuthenticatedAccount, new_password: str
    ) -> None:
        new_hash = self._password_hasher.hash(new_password)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    affected = cursor.execute(
                        """
                        UPDATE user_info
                        SET password = %s
                        WHERE id = %s AND status = %s AND password = %s
                        """,
                        (
                            new_hash,
                            account.principal.principal_id,
                            "ACTIVE",
                            account.stored_password_hash,
                        ),
                    )
                if affected != 1:
                    connection.rollback()
                    raise AuthenticationError("Password changed concurrently")
                connection.commit()
        except pymysql.MySQLError as exc:
            raise AuthenticationError("Unable to update customer password") from exc

    def _account_from_row(
        self,
        row: tuple[object, ...] | None,
        password: str,
        principal_type: PrincipalType,
        *,
        job: object | None = None,
        dept_id: object | None = None,
    ) -> AuthenticatedAccount | None:
        if row is None:
            return None
        principal_id, stored_hash = row
        if not isinstance(stored_hash, str) or not self._password_hasher.verify(
            password, stored_hash
        ):
            return None
        principal = (
            self._staff_principal(principal_id, job, dept_id)
            if principal_type == "STAFF"
            else SessionPrincipal(principal_type="CUSTOMER", principal_id=str(principal_id))
        )
        return AuthenticatedAccount(
            principal=principal,
            stored_password_hash=stored_hash,
            needs_password_rehash=self._password_hasher.needs_rehash(stored_hash),
        )

    @staticmethod
    def _staff_principal(
        emp_id: object, job: object, dept_id: object
    ) -> SessionPrincipal:
        """将 MySQL 员工权限字段转为可信 Session 主体。"""
        if not isinstance(job, str) or not job.strip():
            raise AuthenticationError("Staff job is required for authorization")
        normalized_job = job.strip()
        if normalized_job not in {"staff", "leader"}:
            raise AuthenticationError("Staff job is not supported for authorization")
        if dept_id is not None and (
            isinstance(dept_id, bool) or not isinstance(dept_id, int) or dept_id < 1
        ):
            raise AuthenticationError("Staff department is invalid for authorization")
        return SessionPrincipal(
            principal_type="STAFF",
            principal_id=str(emp_id),
            job=normalized_job,
            dept_id=dept_id,
        )
