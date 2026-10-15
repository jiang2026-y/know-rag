"""回答反馈持久化：校验会话归属、保存快照并提供二十四小时幂等控制。

定义 FeedbackRepository、FeedbackWriteResult 与相关异常；仓储只接受服务层构造的
会话归属散列、权限快照和回答消息标识，不保存用户原始账号或回答正文。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.conversation.domain.feedback import FeedbackReaction
from rail_rag.modules.identity.infrastructure.staff_access_repository import IdempotencyConflictError, IdempotencyInProgressError, IdempotencyReplay


class FeedbackRepositoryError(RuntimeError):
    """回答反馈或其幂等记录持久化不可用时抛出。"""


class FeedbackAnswerNotFoundError(LookupError):
    """主体无权访问、或目标不是已完成助手回答时抛出。"""


@dataclass(frozen=True)
class FeedbackWriteResult:
    """已保存或更新的用户反馈结果。"""

    message_id: str
    reaction: FeedbackReaction
    expires_at: datetime

    def as_payload(self) -> dict[str, str]:
        """转换为可放入幂等响应缓存的安全数据。"""
        return {"message_id": self.message_id, "reaction": self.reaction, "expires_at": self.expires_at.isoformat()}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FeedbackWriteResult":
        """从已完成幂等响应中恢复反馈结果。"""
        message_id, reaction, expires_at = payload.get("message_id"), payload.get("reaction"), payload.get("expires_at")
        if not isinstance(message_id, str) or reaction not in ("LIKE", "DISLIKE") or not isinstance(expires_at, str):
            raise FeedbackRepositoryError("Feedback idempotency response is invalid")
        try:
            return cls(message_id, reaction, datetime.fromisoformat(expires_at))
        except ValueError as exc:
            raise FeedbackRepositoryError("Feedback idempotency response is invalid") from exc


class FeedbackRepository:
    """读写一年保留的回答赞踩反馈与共享幂等记录。"""

    _ENDPOINT = "POST:/api/messages/{message_id}/feedback"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def claim_idempotency(self, actor_id: str, key: str, request_digest: str) -> IdempotencyReplay | None:
        """声明反馈写入幂等键；已完成的相同请求返回首次结果。"""
        return await asyncio.to_thread(self._claim_idempotency, actor_id, key, request_digest)

    async def save_feedback(self, *, actor_id: str, message_id: str, reaction: FeedbackReaction, permission_snapshot: dict[str, object], trace_id: str) -> FeedbackWriteResult:
        """确认回答归属后，创建或更新主体对该回答的唯一反馈。"""
        return await asyncio.to_thread(self._save_feedback, actor_id, message_id, reaction, permission_snapshot, trace_id)

    async def complete_idempotency(self, actor_id: str, key: str, payload: dict[str, str]) -> None:
        """持久化成功响应，以便网络重放不重复更新反馈。"""
        await asyncio.to_thread(self._complete_idempotency, actor_id, key, payload)

    async def release_idempotency(self, actor_id: str, key: str) -> None:
        """在反馈写入失败时释放未完成声明。"""
        await asyncio.to_thread(self._release_idempotency, actor_id, key)

    @staticmethod
    def request_digest(message_id: str, reaction: FeedbackReaction) -> str:
        """生成包含回答和赞踩值的稳定请求摘要。"""
        data = json.dumps({"message_id": message_id, "reaction": reaction}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(host=self._settings.mysql_host, port=self._settings.mysql_port, user=self._settings.mysql_user, password=self._settings.mysql_password.get_secret_value(), database=self._settings.mysql_database, charset="utf8mb4", autocommit=False, connect_timeout=self._settings.mysql_connect_timeout_seconds)

    def _claim_idempotency(self, actor_id: str, key: str, request_digest: str) -> IdempotencyReplay | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM api_idempotency_record WHERE expires_at <= UTC_TIMESTAMP()")
                    cursor.execute("DELETE FROM chat_answer_feedback WHERE expires_at <= UTC_TIMESTAMP()")
                    try:
                        cursor.execute("""INSERT INTO api_idempotency_record (actor_emp_id, endpoint, idempotency_key, request_digest, state, expires_at) VALUES (%s, %s, %s, %s, 'PROCESSING', DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR))""", (actor_id, self._ENDPOINT, key, request_digest))
                    except pymysql.IntegrityError:
                        cursor.execute("""SELECT request_digest, state, response_payload FROM api_idempotency_record WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s FOR UPDATE""", (actor_id, self._ENDPOINT, key))
                        row = cursor.fetchone()
                        if row is None:
                            raise FeedbackRepositoryError("Feedback idempotency record is unavailable")
                        existing_digest, state, response_payload = row
                        if existing_digest != request_digest:
                            raise IdempotencyConflictError("Idempotency key was used by a different request")
                        if state != "SUCCEEDED" or response_payload is None:
                            raise IdempotencyInProgressError("Idempotency request is still processing")
                        connection.commit()
                        return IdempotencyReplay(payload=json.loads(response_payload))
                connection.commit()
                return None
        except (IdempotencyConflictError, IdempotencyInProgressError, FeedbackRepositoryError):
            raise
        except (pymysql.MySQLError, json.JSONDecodeError) as exc:
            raise FeedbackRepositoryError("Unable to claim feedback idempotency key") from exc

    def _save_feedback(self, actor_id: str, message_id: str, reaction: FeedbackReaction, permission_snapshot: dict[str, object], trace_id: str) -> FeedbackWriteResult:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("""SELECT conversation.conversation_id, message.model_name, message.rag_references FROM chat_conversation conversation JOIN chat_message message ON message.conversation_id = conversation.conversation_id WHERE conversation.user_id = %s AND conversation.status = %s AND conversation.deleted = 0 AND message.message_id = %s AND message.type = %s AND message.deleted = 0 FOR UPDATE""", (actor_id, "ACTIVE", message_id, "ASSISTANT"))
                    answer = cursor.fetchone()
                    if answer is None:
                        raise FeedbackAnswerNotFoundError("Assistant answer was not found")
                    conversation_id, model_name, citations = answer
                    if not isinstance(conversation_id, str):
                        raise FeedbackRepositoryError("Feedback answer data is invalid")
                    cursor.execute("""INSERT INTO chat_answer_feedback (actor_id, conversation_id, message_id, reaction, model_name, citations, permission_snapshot, trace_id, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 YEAR)) ON DUPLICATE KEY UPDATE conversation_id = VALUES(conversation_id), reaction = VALUES(reaction), model_name = VALUES(model_name), citations = VALUES(citations), permission_snapshot = VALUES(permission_snapshot), trace_id = VALUES(trace_id), expires_at = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 YEAR)""", (actor_id, conversation_id, message_id, reaction, model_name if isinstance(model_name, str) else None, self._json_value(citations), json.dumps(permission_snapshot, ensure_ascii=False, separators=(",", ":")), trace_id))
                    cursor.execute("SELECT expires_at FROM chat_answer_feedback WHERE actor_id = %s AND message_id = %s", (actor_id, message_id))
                    row = cursor.fetchone()
                    if row is None or not isinstance(row[0], datetime):
                        raise FeedbackRepositoryError("Feedback expiry data is invalid")
                connection.commit()
                return FeedbackWriteResult(message_id, reaction, row[0])
        except (FeedbackAnswerNotFoundError, FeedbackRepositoryError):
            raise
        except pymysql.MySQLError as exc:
            raise FeedbackRepositoryError("Unable to save answer feedback") from exc

    def _complete_idempotency(self, actor_id: str, key: str, payload: dict[str, str]) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("""UPDATE api_idempotency_record SET state = 'SUCCEEDED', response_payload = %s WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s""", (json.dumps(payload, separators=(",", ":")), actor_id, self._ENDPOINT, key))
                connection.commit()
        except pymysql.MySQLError as exc:
            raise FeedbackRepositoryError("Unable to complete feedback idempotency record") from exc

    def _release_idempotency(self, actor_id: str, key: str) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("""DELETE FROM api_idempotency_record WHERE actor_emp_id = %s AND endpoint = %s AND idempotency_key = %s AND state = 'PROCESSING'""", (actor_id, self._ENDPOINT, key))
                connection.commit()
        except pymysql.MySQLError as exc:
            raise FeedbackRepositoryError("Unable to release feedback idempotency key") from exc

    @staticmethod
    def _json_value(value: object) -> str | None:
        """将 MySQL 驱动返回的 JSON 值规范为可再次写入的 JSON 文本。"""
        if value is None:
            return None
        if isinstance(value, str):
            try:
                json.loads(value)
                return value
            except json.JSONDecodeError:
                return None
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            return None
