"""HR 初始密码用例：编排凭据摘要、幂等、会话撤销与安全开户结果。

定义 StaffPasswordProvisionService 和 StaffPasswordProvisionResult；服务仅初始化空密码员工，
使用 Argon2id 生成摘要，在写入前撤销目标员工 Redis Session，并不保存或返回明文密码。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.infrastructure.password_hasher import PasswordHasher
from rail_rag.modules.identity.infrastructure.session_service import RedisSessionService
from rail_rag.modules.identity.infrastructure.staff_access_repository import IdempotencyReplay
from rail_rag.modules.identity.infrastructure.staff_password_provision_repository import (
    StaffPasswordProvisionRepository,
)


@dataclass(frozen=True)
class StaffPasswordProvisionResult:
    """HR 调用方可接收的初始密码开户结果。"""

    emp_id: str
    password_format: str
    revoked_sessions: int

    def as_payload(self) -> dict[str, object]:
        """转换为无敏感字段的幂等响应。"""
        return {
            "emp_id": self.emp_id,
            "password_format": self.password_format,
            "revoked_sessions": self.revoked_sessions,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "StaffPasswordProvisionResult":
        """从首次成功的幂等响应恢复结果。"""
        emp_id = payload.get("emp_id")
        password_format = payload.get("password_format")
        revoked_sessions = payload.get("revoked_sessions")
        if (
            not isinstance(emp_id, str)
            or password_format != "ARGON2ID"
            or isinstance(revoked_sessions, bool)
            or not isinstance(revoked_sessions, int)
        ):
            raise ValueError("Idempotency response is invalid")
        return cls(emp_id, password_format, revoked_sessions)


class StaffPasswordProvisionService:
    """提供给可信 HR 服务的已存在员工初始密码用例。"""

    def __init__(
        self,
        settings: Settings,
        repository: StaffPasswordProvisionRepository,
        sessions: RedisSessionService,
        password_hasher: PasswordHasher,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._password_hasher = password_hasher
        token = settings.internal_hr_service_token
        self._request_hmac_key = token.get_secret_value().encode("utf-8") if token else b""
        self._service_name = settings.internal_hr_service_name.strip()

    async def provision_initial_password(
        self,
        emp_id: str,
        initial_password: str,
        idempotency_key: str,
        trace_id: str,
    ) -> StaffPasswordProvisionResult:
        """为满足条件的员工初始化密码；调用失败时绝不写入部分密码状态。"""
        normalized_emp_id = emp_id.strip()
        normalized_key = idempotency_key.strip()
        if not normalized_emp_id or not normalized_key or not initial_password:
            raise ValueError("Employee ID, initial password and idempotency key are required")
        service_actor = f"SERVICE:{self._service_name}"
        digest = self._request_digest(normalized_emp_id, initial_password)
        replay = await self._repository.claim_idempotency(service_actor, normalized_key, digest)
        if replay is not None:
            return self._result_from_replay(replay)
        try:
            await self._repository.ensure_eligible(normalized_emp_id)
            revoked_sessions = await self._sessions.revoke_staff_sessions(normalized_emp_id)
            password_hash = self._password_hasher.hash(initial_password)
            provisioned = await self._repository.provision_and_complete(
                service_actor,
                self._service_name,
                normalized_emp_id,
                password_hash,
                normalized_key,
                trace_id,
                revoked_sessions,
            )
            return StaffPasswordProvisionResult(
                emp_id=provisioned.emp_id,
                password_format="ARGON2ID",
                revoked_sessions=revoked_sessions,
            )
        except Exception:
            await self._repository.release_idempotency(service_actor, normalized_key)
            raise

    def _request_digest(self, emp_id: str, initial_password: str) -> str:
        """使用内部服务密钥生成可用于幂等比较、不可离线猜解密码的 HMAC。"""
        if not self._request_hmac_key:
            raise RuntimeError("Internal HR service token is not configured")
        payload = f"{emp_id}\x00{initial_password}".encode("utf-8")
        return hmac.new(self._request_hmac_key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _result_from_replay(replay: IdempotencyReplay) -> StaffPasswordProvisionResult:
        """恢复已完成请求的安全响应。"""
        return StaffPasswordProvisionResult.from_payload(replay.payload)
