"""验证会话临时标题持久化与不阻塞主流程的异步标题更新。"""

import asyncio

import pytest

from rail_rag.modules.conversation.infrastructure.conversation_repository import StoredChatConversation
from rail_rag.modules.conversation.application.conversation_service import ChatConversationService


class FakeConversationRepository:
    """记录会话写入和标题更新，替代 MySQL。"""

    def __init__(self) -> None:
        self.creates: list[tuple[str, str, str, str, str]] = []
        self.updates: list[tuple[str, str, str]] = []

    async def create_with_first_message(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
        message_id: str,
        first_message: str,
    ) -> StoredChatConversation:
        self.creates.append((conversation_id, user_id, title, message_id, first_message))
        return StoredChatConversation(conversation_id=conversation_id, title=title)

    async def update_title_if_current(
        self, conversation_id: str, expected_title: str, title: str
    ) -> bool:
        self.updates.append((conversation_id, expected_title, title))
        return True


class FakeTitleClient:
    """按测试需要生成标题或模拟模型故障。"""

    def __init__(self, title: str | None = None) -> None:
        self.title = title
        self.messages: list[str] = []

    async def generate(self, first_message: str) -> str:
        self.messages.append(first_message)
        if self.title is None:
            raise RuntimeError("model unavailable")
        return self.title


@pytest.mark.asyncio
async def test_creates_temporary_title_then_updates_it_in_background() -> None:
    repository = FakeConversationRepository()
    client = FakeTitleClient("知识库检索设计")
    service = ChatConversationService(repository, client)

    response = await service.create_conversation("user-1", "如何设计知识库检索系统？")

    assert response.title == "如何设计知识库检索系统？"
    assert response.title_pending is True
    assert repository.creates[0][1] == "user-1"
    assert repository.creates[0][4] == "如何设计知识库检索系统？"
    await asyncio.wait_for(asyncio.gather(*service._title_tasks), timeout=1)
    assert client.messages == ["如何设计知识库检索系统？"]
    assert repository.updates == [
        (response.conversation_id, "如何设计知识库检索系统？", "知识库检索设计")
    ]


@pytest.mark.asyncio
async def test_title_failure_keeps_temporary_title_and_does_not_raise() -> None:
    repository = FakeConversationRepository()
    service = ChatConversationService(repository, FakeTitleClient())

    response = await service.create_conversation("user-1", "测试标题生成失败")

    await asyncio.wait_for(asyncio.gather(*service._title_tasks), timeout=1)
    assert response.title == "测试标题生成失败"
    assert repository.updates == []
