"""流式问答应用服务测试：验证授权检索、多轮记忆、SSE 事件和回答持久化规则。

定义内存替身及多项测试，覆盖成功回答的增量与引用事件、模型失败不保存部分回答、
历史注入顺序与字符预算、历史读取失败降级，以及通用对话带历史但不带证据。
"""

from collections.abc import AsyncIterator

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.conversation.application.chat_answer_service import ChatAnswerService
from rail_rag.modules.conversation.infrastructure.conversation_repository import (
    StoredConversationMessage,
    StoredUserMessage,
)
from rail_rag.modules.identity.domain.principal import (
    SessionPrincipal,
    conversation_owner_id,
)
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalResult,
)
from rail_rag.modules.qa.domain.intent import (
    IntentEntities,
    IntentRecognitionResult,
)


class FakeConversationRepository:
    """记录会话消息读取、追加与成功回答持久化调用。"""

    def __init__(self) -> None:
        self.saved_answers: list[tuple[object, ...]] = []
        self.history: list[StoredConversationMessage] = []
        self.history_error: Exception | None = None
        self.history_requests: list[tuple[str, str, int, str | None]] = []

    async def get_owned_user_message(self, conversation_id, owner_id, message_id):
        assert conversation_id == "conversation-1"
        assert message_id == "message-1"
        return StoredUserMessage(message_id, "交付检查流程是什么？")

    async def create_user_message(self, conversation_id, owner_id, message_id, content):
        return StoredUserMessage(message_id, content)

    async def list_recent_messages(
        self, conversation_id, owner_id, limit, *, exclude_message_id=None
    ):
        self.history_requests.append(
            (conversation_id, owner_id, limit, exclude_message_id)
        )
        if self.history_error is not None:
            raise self.history_error
        return list(self.history)

    async def append_assistant_message(
        self, conversation_id, message_id, content, model_name, rag_references, in_reply_to
    ):
        self.saved_answers.append(
            (conversation_id, message_id, content, model_name, rag_references, in_reply_to)
        )


class FakeRetrievalService:
    """返回已授权的固定知识证据，并记录提问主体。"""

    def __init__(self) -> None:
        self.principal: SessionPrincipal | None = None

    async def search(self, principal, question, *, limit):
        self.principal = principal
        assert question == "交付检查流程是什么？"
        assert limit == 5
        return [KnowledgeRetrievalResult("chunk-1", "检查轮胎与随车工具。", 0.9, 7)]


class FakeAnswerClient:
    """按测试配置流式输出文本或抛出模型异常，并记录收到的用户提示。"""

    def __init__(self, *, fail: bool = False, expect_evidence: bool = True) -> None:
        self.fail = fail
        self.expect_evidence = expect_evidence
        self.prompts: list[str] = []

    async def stream_answer(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        self.prompts.append(user_prompt)
        if self.expect_evidence:
            assert "已授权知识证据" in user_prompt
        if self.fail:
            raise RuntimeError("upstream failed")
        yield "请先检查轮胎"
        yield "与随车工具。"


class FakeIntentService:
    """返回可配置意图结果，以验证检索路由不会由客户端输入决定。"""

    def __init__(self, *, related: bool = True) -> None:
        self.related = related

    async def recognize(self, principal, question):
        return IntentRecognitionResult(
            related=self.related,
            intent="车辆使用与技术指导" if self.related else "闲聊与通用问答",
            entities=IntentEntities(),
            reasoning="测试意图结果。",
        )


def principal() -> SessionPrincipal:
    """创建一名普通员工的可信 Session 主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id="EMP-1", job="staff")


@pytest.mark.asyncio
async def test_streams_authorized_answer_citations_and_persists_after_done() -> None:
    repository = FakeConversationRepository()
    retrieval = FakeRetrievalService()
    service = ChatAnswerService(
        Settings(), repository, retrieval, FakeAnswerClient(), FakeIntentService()  # type: ignore[arg-type]
    )
    prepared = await service.prepare(principal(), "conversation-1", message_id="message-1")

    events = [event async for event in service.stream(prepared, "trace-1") if event is not None]

    assert [event.event for event in events] == [
        "query_rewrite",
        "answer_delta",
        "answer_delta",
        "citation",
        "done",
    ]
    assert events[-1].payload["status"] == "completed"
    assert events[0].payload["original_query"] == "交付检查流程是什么？"
    assert events[0].payload["rewritten_query"] == "交付检查流程是什么？"
    assert repository.saved_answers[0][2] == "请先检查轮胎与随车工具。"
    assert repository.saved_answers[0][4] == [
        {
            "citation_id": "citation-1",
            "document_id": 7,
            "chunk_id": "chunk-1",
            "source_name": "企业知识库文档",
            "section": None,
            "source_type": "knowledge_base",
        }
    ]
    citation_event = next(event for event in events if event.event == "citation")
    assert "document_id" not in citation_event.payload["citation"]
    assert "chunk_id" not in citation_event.payload["citation"]
    assert retrieval.principal == principal()


@pytest.mark.asyncio
async def test_model_failure_emits_error_and_does_not_persist_partial_answer() -> None:
    repository = FakeConversationRepository()
    service = ChatAnswerService(
        Settings(), repository, FakeRetrievalService(), FakeAnswerClient(fail=True), FakeIntentService()  # type: ignore[arg-type]
    )
    prepared = await service.prepare(principal(), "conversation-1", message_id="message-1")

    events = [event async for event in service.stream(prepared, "trace-1") if event is not None]

    assert [event.event for event in events] == ["query_rewrite", "error", "done"]
    assert events[-1].payload["status"] == "error"
    assert repository.saved_answers == []


async def drain(stream) -> None:
    """消费 SSE 事件流而不关心事件内容。"""
    async for _ in stream:
        pass


@pytest.mark.asyncio
async def test_recent_history_is_injected_before_question_and_evidence() -> None:
    repository = FakeConversationRepository()
    repository.history = [
        StoredConversationMessage("message-0", "user", "交付检查要注意什么？", []),
        StoredConversationMessage("message-1", "assistant", "先检查轮胎与随车工具。", []),
    ]
    answer_client = FakeAnswerClient()
    service = ChatAnswerService(
        Settings(), repository, FakeRetrievalService(), answer_client, FakeIntentService()  # type: ignore[arg-type]
    )

    prepared = await service.prepare(principal(), "conversation-1", message_id="message-1")
    events = [event async for event in service.stream(prepared, "trace-1") if event is not None]

    prompt = answer_client.prompts[0]
    assert prompt.index("历史对话：") < prompt.index("用户问题：")
    assert prompt.index("用户问题：") < prompt.index("已授权知识证据：")
    assert "用户：交付检查要注意什么？" in prompt
    assert "助手：先检查轮胎与随车工具。" in prompt
    assert repository.history_requests == [
        ("conversation-1", conversation_owner_id(principal()), 10, "message-1")
    ]
    # 历史只进入提示词，不参与引用事件与引用持久化。
    citation_events = [event for event in events if event.event == "citation"]
    assert len(citation_events) == 1
    assert repository.saved_answers[0][4] == [
        {
            "citation_id": "citation-1",
            "document_id": 7,
            "chunk_id": "chunk-1",
            "source_name": "企业知识库文档",
            "section": None,
            "source_type": "knowledge_base",
        }
    ]


@pytest.mark.asyncio
async def test_history_character_budget_keeps_the_most_recent_messages() -> None:
    repository = FakeConversationRepository()
    repository.history = [
        StoredConversationMessage("message-0", "user", "旧" * 30, []),
        StoredConversationMessage("message-1", "assistant", "新回答", []),
    ]
    answer_client = FakeAnswerClient()
    service = ChatAnswerService(
        Settings(chat_history_max_characters=12),
        repository,
        FakeRetrievalService(),
        answer_client,
        FakeIntentService(),  # type: ignore[arg-type]
    )

    prepared = await service.prepare(principal(), "conversation-1", message_id="message-1")
    await drain(service.stream(prepared, "trace-1"))

    prompt = answer_client.prompts[0]
    assert "助手：新回答" in prompt
    assert "旧" * 30 not in prompt


@pytest.mark.asyncio
async def test_history_read_failure_degrades_to_no_history() -> None:
    repository = FakeConversationRepository()
    repository.history_error = RuntimeError("mysql is unavailable")
    answer_client = FakeAnswerClient()
    service = ChatAnswerService(
        Settings(), repository, FakeRetrievalService(), answer_client, FakeIntentService()  # type: ignore[arg-type]
    )

    prepared = await service.prepare(principal(), "conversation-1", message_id="message-1")
    events = [event async for event in service.stream(prepared, "trace-1") if event is not None]

    assert [event.event for event in events] == [
        "query_rewrite",
        "answer_delta",
        "answer_delta",
        "citation",
        "done",
    ]
    assert "历史对话：" not in answer_client.prompts[0]


@pytest.mark.asyncio
async def test_general_conversation_keeps_history_without_authorized_evidence() -> None:
    repository = FakeConversationRepository()
    repository.history = [
        StoredConversationMessage("message-0", "user", "你上次提到哪些检查项？", [])
    ]
    answer_client = FakeAnswerClient(expect_evidence=False)
    service = ChatAnswerService(
        Settings(),
        repository,
        FakeRetrievalService(),
        answer_client,
        FakeIntentService(related=False),  # type: ignore[arg-type]
    )

    prepared = await service.prepare(principal(), "conversation-1", message_id="message-1")
    await drain(service.stream(prepared, "trace-1"))

    prompt = answer_client.prompts[0]
    assert "历史对话：" in prompt
    assert "你上次提到哪些检查项？" in prompt
    assert "已授权知识证据" not in prompt


@pytest.mark.asyncio
async def test_non_automotive_question_skips_knowledge_retrieval() -> None:
    class ForbiddenRetrievalService:
        async def search(self, principal, question, *, limit):
            raise AssertionError("非汽车问题不应访问知识检索")

    class GeneralAnswerClient:
        async def stream_answer(self, system_prompt, user_prompt):
            assert "企业内部知识" in system_prompt
            assert "已授权知识证据" not in user_prompt
            yield "今天天气请以当地预报为准。"

    repository = FakeConversationRepository()
    service = ChatAnswerService(
        Settings(),
        repository,
        ForbiddenRetrievalService(),  # type: ignore[arg-type]
        GeneralAnswerClient(),  # type: ignore[arg-type]
        FakeIntentService(related=False),  # type: ignore[arg-type]
    )
    prepared = await service.prepare(principal(), "conversation-1", message="今天天气怎么样？")
    events = [event async for event in service.stream(prepared, "trace-1") if event is not None]

    assert prepared.evidence == []
    assert [event.event for event in events] == ["query_rewrite", "answer_delta", "done"]
    assert repository.saved_answers[0][4] == []
