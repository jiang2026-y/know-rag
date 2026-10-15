"""会话服务：即时保存临时标题，并在后台生成和更新正式标题。"""

from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from rail_rag.modules.conversation.domain.conversation import ConversationCreatedResponse
from rail_rag.modules.conversation.infrastructure.dashscope_title_client import DashScopeTitleClient
from rail_rag.modules.conversation.infrastructure.conversation_repository import ChatConversationRepository, ConversationSnapshot, StoredChatConversation


class ChatConversationService:
    """创建首轮会话，并使标题生成任务不阻塞后续流式回答。"""

    def __init__(
        self,
        repository: ChatConversationRepository,
        title_client: DashScopeTitleClient,
        *,
        title_max_length: int = 20,
    ) -> None:
        self._repository = repository
        self._title_client = title_client
        self._title_max_length = title_max_length
        self._title_tasks: set[asyncio.Task[None]] = set()

    async def create_conversation(
        self, user_id: str, first_message: str
    ) -> ConversationCreatedResponse:
        """保存临时标题和首条消息后，立即调度非阻塞的标题生成任务。"""
        normalized_user_id = user_id.strip()
        normalized_message = first_message.strip()
        if not normalized_user_id:
            raise ValueError("user_id must not be empty")
        if not normalized_message:
            raise ValueError("first_message must not be empty")

        title = self._temporary_title(normalized_message)
        conversation_id = str(uuid4())
        message_id = str(uuid4())
        await self._repository.create_with_first_message(
            conversation_id,
            normalized_user_id,
            title,
            message_id,
            normalized_message,
        )
        task = asyncio.create_task(
            self._generate_and_update_title(conversation_id, title, normalized_message)
        )
        self._title_tasks.add(task)
        task.add_done_callback(self._title_tasks.discard)
        return ConversationCreatedResponse(
            conversation_id=conversation_id,
            first_message_id=message_id,
            title=title,
            title_pending=True,
        )

    async def list_conversations(self, user_id: str, limit: int = 50) -> list[StoredChatConversation]:
        """返回当前主体拥有的会话列表。"""
        return await self._repository.list_owned_conversations(user_id, limit)

    async def get_snapshot(self, user_id: str, conversation_id: str) -> ConversationSnapshot | None:
        """返回当前主体拥有的会话历史。"""
        return await self._repository.get_owned_snapshot(conversation_id, user_id)

    def _temporary_title(self, first_message: str) -> str:
        title = re.sub(r"\s+", " ", first_message).strip()[: self._title_max_length]
        return title.rstrip() or "新对话"

    async def _generate_and_update_title(
        self, conversation_id: str, temporary_title: str, first_message: str
    ) -> None:
        try:
            title = await self._title_client.generate(first_message)
            await self._repository.update_title_if_current(
                conversation_id, temporary_title, title
            )
        except Exception:
            # 标题属于辅助信息；模型或数据库异常均不得影响正在进行的回答流。
            return
