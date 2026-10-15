"""Redis 服务端会话：保存可信身份主体，并支持顾客与员工全量会话撤销。

定义 SessionBackendError、InvalidSessionError 和 RedisSessionService；服务用于创建、读取、撤销当前
Session，以及按主体 ID 撤销全部 Session。顾客和员工会话都使用 Redis 索引和会话代次，
防止密码或权限变更后旧会话继续访问。
"""

import json
import secrets

from redis.asyncio import Redis
from redis.exceptions import RedisError

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.config.settings import Settings


class SessionBackendError(RuntimeError):
    """Redis 会话存储不可用时抛出。"""


class InvalidSessionError(RuntimeError):
    """请求没有携带有效服务端会话时抛出。"""


class RedisSessionService:
    """使用 Redis TTL 保存服务端会话主体。"""

    _KEY_PREFIX = "rail_rag:session:"
    _STAFF_SESSIONS_KEY_PREFIX = "rail_rag:staff-sessions:"
    _STAFF_SESSION_EPOCH_KEY_PREFIX = "rail_rag:staff-session-epoch:"
    _CUSTOMER_SESSIONS_KEY_PREFIX = "rail_rag:customer-sessions:"
    _CUSTOMER_SESSION_EPOCH_KEY_PREFIX = "rail_rag:customer-session-epoch:"
    _REVOKE_SESSIONS_SCRIPT = """
local next_epoch = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
local session_ids = redis.call('SMEMBERS', KEYS[2])
for _, session_id in ipairs(session_ids) do
    redis.call('DEL', ARGV[2] .. session_id)
end
redis.call('DEL', KEYS[2])
return #session_ids
"""

    def __init__(self, settings: Settings, *, client: Redis | None = None) -> None:
        password = (
            settings.redis_password.get_secret_value()
            if settings.redis_password is not None
            else None
        )
        self._ttl_seconds = settings.auth_session_ttl_seconds
        self._client = client or Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=password or None,
            db=settings.redis_database,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_connect_timeout_seconds,
        )

    @property
    def ttl_seconds(self) -> int:
        """返回新建会话的有效期。"""
        return self._ttl_seconds

    async def create(self, principal: SessionPrincipal) -> str:
        """创建不可预测的会话 ID，并为员工建立按工号撤销索引。"""
        session_id = secrets.token_urlsafe(32)
        try:
            payload = await self._serialize_session(principal)
            created = await self._client.set(
                self._key(session_id), payload, ex=self._ttl_seconds, nx=True
            )
            if not created:
                raise SessionBackendError("Unable to create a unique authentication session")
            if principal.principal_type == "STAFF":
                await self._client.sadd(
                    self._staff_sessions_key(principal.principal_id), session_id
                )
                await self._client.expire(
                    self._staff_sessions_key(principal.principal_id), self._ttl_seconds
                )
            else:
                await self._client.sadd(
                    self._customer_sessions_key(principal.principal_id), session_id
                )
                await self._client.expire(
                    self._customer_sessions_key(principal.principal_id), self._ttl_seconds
                )
        except RedisError as exc:
            await self._delete_session_after_create_failure(session_id)
            raise SessionBackendError("Redis session service is unavailable") from exc
        return session_id

    async def get_principal(self, session_id: str | None) -> SessionPrincipal:
        """读取并校验会话主体；不存在、过期或损坏的会话都视为未认证。"""
        if not session_id:
            raise InvalidSessionError("Authentication session is required")
        try:
            payload = await self._client.get(self._key(session_id))
        except RedisError as exc:
            raise SessionBackendError("Redis session service is unavailable") from exc
        if not payload:
            raise InvalidSessionError("Authentication session is invalid or expired")
        try:
            principal, staff_session_epoch, customer_session_epoch = self._deserialize_session(payload)
            if principal.principal_type == "STAFF":
                if staff_session_epoch is None:
                    raise ValueError("Staff session epoch is missing")
                current_epoch = await self._get_staff_session_epoch(principal.principal_id)
                if staff_session_epoch != current_epoch:
                    raise InvalidSessionError("Authentication session has been revoked")
            else:
                current_epoch = await self._get_customer_session_epoch(principal.principal_id)
                if customer_session_epoch is not None and customer_session_epoch != current_epoch:
                    raise InvalidSessionError("Authentication session has been revoked")
                if customer_session_epoch is None and current_epoch != 0:
                    raise InvalidSessionError("Authentication session has been revoked")
            return principal
        except InvalidSessionError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidSessionError("Authentication session is invalid") from exc

    async def delete(self, session_id: str | None) -> None:
        """撤销当前会话；缺失 Cookie 时按幂等登出处理。"""
        if not session_id:
            return
        try:
            payload = await self._client.get(self._key(session_id))
            await self._client.delete(self._key(session_id))
            if not payload:
                return
            principal, _, _ = self._deserialize_session(payload)
            if principal.principal_type == "STAFF":
                await self._client.srem(
                    self._staff_sessions_key(principal.principal_id), session_id
                )
            else:
                await self._client.srem(
                    self._customer_sessions_key(principal.principal_id), session_id
                )
        except RedisError as exc:
            raise SessionBackendError("Redis session service is unavailable") from exc

    async def revoke_staff_sessions(self, emp_id: str) -> int:
        """立即撤销指定员工的全部 Session，并返回物理删除的会话数量。"""
        normalized_emp_id = emp_id.strip()
        if not normalized_emp_id:
            raise ValueError("Employee ID is required")
        try:
            result = await self._client.eval(
                self._REVOKE_SESSIONS_SCRIPT,
                2,
                self._staff_session_epoch_key(normalized_emp_id),
                self._staff_sessions_key(normalized_emp_id),
                self._ttl_seconds,
                self._KEY_PREFIX,
            )
        except RedisError as exc:
            raise SessionBackendError("Redis session service is unavailable") from exc
        return int(result)

    async def revoke_customer_sessions(self, user_id: str) -> int:
        """立即撤销指定顾客的全部 Session，并使历史未索引 Session 失效。"""
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("Customer user ID is required")
        try:
            result = await self._client.eval(
                self._REVOKE_SESSIONS_SCRIPT,
                2,
                self._customer_session_epoch_key(normalized_user_id),
                self._customer_sessions_key(normalized_user_id),
                self._ttl_seconds,
                self._KEY_PREFIX,
            )
        except RedisError as exc:
            raise SessionBackendError("Redis session service is unavailable") from exc
        return int(result)

    async def _serialize_session(self, principal: SessionPrincipal) -> str:
        """序列化会话；员工会话固定记录创建时的撤销代次。"""
        record: dict[str, object] = {"principal": principal.model_dump(mode="json")}
        if principal.principal_type == "STAFF":
            record["staff_session_epoch"] = await self._get_staff_session_epoch(
                principal.principal_id
            )
        else:
            record["customer_session_epoch"] = await self._get_customer_session_epoch(
                principal.principal_id
            )
        return json.dumps(record, separators=(",", ":"))

    def _deserialize_session(
        self, payload: str
    ) -> tuple[SessionPrincipal, int | None, int | None]:
        """读取当前会话格式，并兼容历史顾客会话的主体 JSON。"""
        record = json.loads(payload)
        if not isinstance(record, dict):
            raise ValueError("Session payload must be an object")
        if "principal" not in record:
            return SessionPrincipal.model_validate(record), None, None
        principal = SessionPrincipal.model_validate(record["principal"])
        staff_session_epoch = record.get("staff_session_epoch")
        if staff_session_epoch is not None and (
            isinstance(staff_session_epoch, bool) or not isinstance(staff_session_epoch, int)
        ):
            raise ValueError("Staff session epoch is invalid")
        customer_session_epoch = record.get("customer_session_epoch")
        if customer_session_epoch is not None and (
            isinstance(customer_session_epoch, bool)
            or not isinstance(customer_session_epoch, int)
            or customer_session_epoch < 0
        ):
            raise ValueError("Customer session epoch is invalid")
        return principal, staff_session_epoch, customer_session_epoch

    async def _get_staff_session_epoch(self, emp_id: str) -> int:
        """获取员工当前会话代次；不存在时表示从初始代次零开始。"""
        try:
            value = await self._client.get(self._staff_session_epoch_key(emp_id))
        except RedisError as exc:
            raise SessionBackendError("Redis session service is unavailable") from exc
        if value is None:
            return 0
        try:
            epoch = int(value)
        except (TypeError, ValueError) as exc:
            raise SessionBackendError("Redis staff session epoch is invalid") from exc
        if epoch < 0:
            raise SessionBackendError("Redis staff session epoch is invalid")
        return epoch

    async def _get_customer_session_epoch(self, user_id: str) -> int:
        """获取顾客会话代次；不存在时表示从初始代次零开始。"""
        try:
            value = await self._client.get(self._customer_session_epoch_key(user_id))
        except RedisError as exc:
            raise SessionBackendError("Redis session service is unavailable") from exc
        if value is None:
            return 0
        try:
            epoch = int(value)
        except (TypeError, ValueError) as exc:
            raise SessionBackendError("Redis customer session epoch is invalid") from exc
        if epoch < 0:
            raise SessionBackendError("Redis customer session epoch is invalid")
        return epoch

    async def _delete_session_after_create_failure(self, session_id: str) -> None:
        """创建员工索引失败时尽力删除尚未返回给客户端的 Session。"""
        try:
            await self._client.delete(self._key(session_id))
        except RedisError:
            pass

    @classmethod
    def _key(cls, session_id: str) -> str:
        """构造不会与 Redis 锁键冲突的会话键。"""
        return f"{cls._KEY_PREFIX}{session_id}"

    @classmethod
    def _staff_sessions_key(cls, emp_id: str) -> str:
        """构造员工工号对应的活跃 Session 索引键。"""
        return f"{cls._STAFF_SESSIONS_KEY_PREFIX}{emp_id}"

    @classmethod
    def _staff_session_epoch_key(cls, emp_id: str) -> str:
        """构造员工权限变更后的 Session 撤销代次键。"""
        return f"{cls._STAFF_SESSION_EPOCH_KEY_PREFIX}{emp_id}"

    @classmethod
    def _customer_sessions_key(cls, user_id: str) -> str:
        """构造顾客主体对应的活跃 Session 索引键。"""
        return f"{cls._CUSTOMER_SESSIONS_KEY_PREFIX}{user_id}"

    @classmethod
    def _customer_session_epoch_key(cls, user_id: str) -> str:
        """构造顾客密码变更后的 Session 撤销代次键。"""
        return f"{cls._CUSTOMER_SESSION_EPOCH_KEY_PREFIX}{user_id}"
