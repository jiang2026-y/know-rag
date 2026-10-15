"""会话仓储：原子写入会话和首条消息，并安全更新仍为临时值的会话标题。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any

import pymysql

from rail_rag.config.settings import Settings


class ChatConversationRepositoryError(RuntimeError):
    """会话或消息持久化失败时抛出。"""


@dataclass(frozen=True)
class StoredChatConversation:
    """已保存会话的最小信息。"""

    conversation_id: str
    title: str


@dataclass(frozen=True)
class StoredUserMessage:
    """已验证归属于当前会话主体的用户提问。"""

    message_id: str
    content: str


@dataclass(frozen=True)
class StoredConversationMessage:
    """会话历史中可安全展示的消息。"""

    message_id: str
    role: str
    content: str
    citations: list[dict[str, Any]]


@dataclass(frozen=True)
class ConversationSnapshot:
    """当前主体拥有的会话及其消息快照。"""

    conversation_id: str
    title: str
    status: str
    messages: list[StoredConversationMessage]


class ChatConversationRepository:
    """读写 chat_conversation 和 chat_message 两张已有的 MySQL 表。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def create_with_first_message(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
        message_id: str,
        first_message: str,
    ) -> StoredChatConversation:
        """在一个 MySQL 事务内保存会话与其第一条 USER 消息。"""
        return await asyncio.to_thread(
            self._create_with_first_message,
            conversation_id,
            user_id,
            title,
            message_id,
            first_message,
        )

    async def update_title_if_current(
        self, conversation_id: str, expected_title: str, title: str
    ) -> bool:
        """仅当标题仍是临时标题时更新，避免覆盖用户的手工修改。"""
        return await asyncio.to_thread(
            self._update_title_if_current, conversation_id, expected_title, title
        )

    async def get_owned_user_message(
        self, conversation_id: str, owner_id: str, message_id: str
    ) -> StoredUserMessage | None:
        """读取当前主体拥有的会话中的一条用户提问。"""
        return await asyncio.to_thread(
            self._get_owned_user_message, conversation_id, owner_id, message_id
        )

    async def create_user_message(
        self, conversation_id: str, owner_id: str, message_id: str, content: str
    ) -> StoredUserMessage | None:
        """向当前主体拥有的活动会话安全追加一条用户提问。"""
        return await asyncio.to_thread(
            self._create_user_message, conversation_id, owner_id, message_id, content
        )

    async def list_owned_conversations(self, owner_id: str, limit: int = 50) -> list[StoredChatConversation]:
        """按主体列出最近的活动会话。"""
        return await asyncio.to_thread(self._list_owned_conversations, owner_id, limit)

    async def get_owned_snapshot(self, conversation_id: str, owner_id: str) -> ConversationSnapshot | None:
        """读取当前主体拥有的会话及其消息历史。"""
        return await asyncio.to_thread(self._get_owned_snapshot, conversation_id, owner_id)

    async def list_recent_messages(
        self,
        conversation_id: str,
        owner_id: str,
        limit: int,
        *,
        exclude_message_id: str | None = None,
    ) -> list[StoredConversationMessage]:
        """按时间正序返回当前主体最近若干条已持久化的文本消息。"""
        if limit < 1:
            return []
        return await asyncio.to_thread(
            self._list_recent_messages,
            conversation_id,
            owner_id,
            limit,
            exclude_message_id,
        )

    async def update_user_message_metadata(
        self, conversation_id: str, message_id: str, metadata: dict[str, object]
    ) -> None:
        """在已校验的用户消息上合并查询改写审计元数据。"""
        await asyncio.to_thread(
            self._update_user_message_metadata, conversation_id, message_id, metadata
        )

    async def append_assistant_message(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
        model_name: str,
        rag_references: list[dict[str, Any]],
        in_reply_to: str,
    ) -> None:
        """仅在完整回答生成后保存助手消息、模型和已授权引用。"""
        await asyncio.to_thread(
            self._append_assistant_message,
            conversation_id,
            message_id,
            content,
            model_name,
            rag_references,
            in_reply_to,
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
            connect_timeout=self._settings.mysql_connect_timeout_seconds,
        )

    def _create_with_first_message(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
        message_id: str,
        first_message: str,
    ) -> StoredChatConversation:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO chat_conversation
                            (conversation_id, user_id, title, status)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (conversation_id, user_id, title, "ACTIVE"),
                    )
                    cursor.execute(
                        """
                        INSERT INTO chat_message
                            (message_id, conversation_id, type, content)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (message_id, conversation_id, "USER", first_message),
                    )
                connection.commit()
            return StoredChatConversation(conversation_id=conversation_id, title=title)
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError(
                f"Unable to create chat conversation: {exc}"
            ) from exc

    def _update_title_if_current(
        self, conversation_id: str, expected_title: str, title: str
    ) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    affected = cursor.execute(
                        """
                        UPDATE chat_conversation
                        SET title = %s
                        WHERE conversation_id = %s
                          AND title = %s
                          AND status = %s
                        """,
                        (title, conversation_id, expected_title, "ACTIVE"),
                    )
                connection.commit()
            return affected == 1
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError(
                f"Unable to update chat title: {exc}"
            ) from exc

    def _get_owned_user_message(
        self, conversation_id: str, owner_id: str, message_id: str
    ) -> StoredUserMessage | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT message.message_id, message.content
                        FROM chat_conversation conversation
                        JOIN chat_message message
                          ON message.conversation_id = conversation.conversation_id
                        WHERE conversation.conversation_id = %s
                          AND conversation.user_id = %s
                          AND conversation.status = %s
                          AND message.message_id = %s
                          AND message.type = %s
                          AND message.deleted = 0
                        """,
                        (conversation_id, owner_id, "ACTIVE", message_id, "USER"),
                    )
                    row = cursor.fetchone()
            if row is None or not isinstance(row[0], str) or not isinstance(row[1], str):
                return None
            return StoredUserMessage(row[0], row[1])
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError(
                f"Unable to read chat message: {exc}"
            ) from exc

    def _create_user_message(
        self, conversation_id: str, owner_id: str, message_id: str, content: str
    ) -> StoredUserMessage | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT 1 FROM chat_conversation
                        WHERE conversation_id = %s AND user_id = %s AND status = %s
                        FOR UPDATE
                        """,
                        (conversation_id, owner_id, "ACTIVE"),
                    )
                    if cursor.fetchone() is None:
                        return None
                    cursor.execute(
                        """
                        INSERT INTO chat_message
                            (message_id, conversation_id, type, content)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (message_id, conversation_id, "USER", content),
                    )
                connection.commit()
            return StoredUserMessage(message_id, content)
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError(
                f"Unable to create chat message: {exc}"
            ) from exc

    def _list_owned_conversations(self, owner_id: str, limit: int) -> list[StoredChatConversation]:
        if not owner_id or limit < 1 or limit > 100:
            raise ValueError("Conversation listing parameters are invalid")
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT conversation_id, title FROM chat_conversation WHERE user_id = %s AND deleted = 0 ORDER BY updated_at DESC LIMIT %s", (owner_id, limit))
                    return [StoredChatConversation(str(row[0]), str(row[1])) for row in cursor.fetchall()]
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError("Unable to list chat conversations") from exc

    def _get_owned_snapshot(self, conversation_id: str, owner_id: str) -> ConversationSnapshot | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT conversation_id, title, status FROM chat_conversation WHERE conversation_id = %s AND user_id = %s AND deleted = 0", (conversation_id, owner_id))
                    conversation = cursor.fetchone()
                    if conversation is None:
                        return None
                    cursor.execute("SELECT message_id, type, content, rag_references FROM chat_message WHERE conversation_id = %s AND deleted = 0 ORDER BY created_at ASC", (conversation_id,))
                    messages = []
                    for message_id, message_type, content, references in cursor.fetchall():
                        try:
                            citations = json.loads(references) if references else []
                        except (TypeError, json.JSONDecodeError):
                            citations = []
                        messages.append(StoredConversationMessage(str(message_id), "assistant" if message_type == "ASSISTANT" else "user", str(content), citations if isinstance(citations, list) else []))
                    return ConversationSnapshot(str(conversation[0]), str(conversation[1]), str(conversation[2]), messages)
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError("Unable to read chat conversation") from exc

    def _list_recent_messages(
        self,
        conversation_id: str,
        owner_id: str,
        limit: int,
        exclude_message_id: str | None,
    ) -> list[StoredConversationMessage]:
        """只读取属于该主体会话的文本消息，并把最近 N 条恢复为时间正序。"""
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT message.message_id, message.type, message.content
                        FROM chat_conversation conversation
                        JOIN chat_message message
                          ON message.conversation_id = conversation.conversation_id
                        WHERE conversation.conversation_id = %s
                          AND conversation.user_id = %s
                          AND conversation.deleted = 0
                          AND message.deleted = 0
                          AND message.type IN (%s, %s)
                          AND message.content IS NOT NULL
                          AND message.content <> ''
                          AND (%s IS NULL OR message.message_id <> %s)
                        ORDER BY message.created_at DESC, message.id DESC
                        LIMIT %s
                        """,
                        (
                            conversation_id,
                            owner_id,
                            "USER",
                            "ASSISTANT",
                            exclude_message_id,
                            exclude_message_id,
                            limit,
                        ),
                    )
                    rows = cursor.fetchall()
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError(
                "Unable to read recent chat messages"
            ) from exc
        messages = [
            StoredConversationMessage(
                str(message_id),
                "assistant" if message_type == "ASSISTANT" else "user",
                str(content),
                [],
            )
            for message_id, message_type, content in rows
        ]
        messages.reverse()
        return messages

    def _update_user_message_metadata(
        self, conversation_id: str, message_id: str, metadata: dict[str, object]
    ) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE chat_message
                        SET metadata = JSON_MERGE_PATCH(COALESCE(metadata, JSON_OBJECT()), %s)
                        WHERE conversation_id = %s AND message_id = %s
                          AND type = %s AND deleted = 0
                        """,
                        (
                            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                            conversation_id,
                            message_id,
                            "USER",
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ChatConversationRepositoryError("User message was not found")
                connection.commit()
        except ChatConversationRepositoryError:
            raise
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError(
                f"Unable to update chat message metadata: {exc}"
            ) from exc

    def _append_assistant_message(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
        model_name: str,
        rag_references: list[dict[str, Any]],
        in_reply_to: str,
    ) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO chat_message
                            (message_id, conversation_id, type, content, model_name, rag_references, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            message_id,
                            conversation_id,
                            "ASSISTANT",
                            content,
                            model_name,
                            json.dumps(rag_references, ensure_ascii=False),
                            json.dumps(
                                {"inReplyTo": in_reply_to, "promptVersion": "p0-rag-v1"},
                                ensure_ascii=False,
                            ),
                        ),
                    )
                connection.commit()
        except pymysql.MySQLError as exc:
            raise ChatConversationRepositoryError(
                f"Unable to save chat answer: {exc}"
            ) from exc
