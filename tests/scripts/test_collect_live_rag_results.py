"""真实链路采集脚本测试：验证主体映射、结果组装、成功/失败/跳过与断点续跑。

定义内存替身与七项测试，覆盖顾客/员工主体、证据与回答提取、模型错误不保留部分回答、
非汽车问题跳过、结果字段结构以及已完成样本识别。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rail_rag.modules.conversation.application.chat_answer_service import PreparedChatAnswer
from rail_rag.modules.conversation.domain.stream_event import ChatStreamEvent
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalResult,
)
from rail_rag.modules.qa.domain.intent import IntentEntities, IntentRecognitionResult
from rail_rag.modules.qa.domain.query_route import QueryRouteResult


def make_sample(
    *,
    sample_id: str = "p0-001",
    principal_type: str = "CUSTOMER",
    related: bool = True,
) -> dict[str, object]:
    """构造一条最小评测样本。"""
    return {
        "id": sample_id,
        "question": "我的保险什么时候到期？",
        "principal_type": principal_type,
        "expected_related": related,
    }


def make_event(event: str, payload: dict[str, object], sequence: int = 1) -> ChatStreamEvent:
    """构造一条带完整公共字段的流式事件。"""
    return ChatStreamEvent(
        event=event,  # type: ignore[arg-type]
        conversation_id="eval-conversation",
        message_id="message-1",
        sequence=sequence,
        trace_id="trace-1",
        payload=payload,
    )


class FakeConversationRepository:
    """记录采集脚本创建的评测会话。"""

    def __init__(self) -> None:
        self.created: list[tuple[str, str, str, str, str]] = []

    async def create_with_first_message(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
        message_id: str,
        first_message: str,
    ) -> None:
        self.created.append((conversation_id, user_id, title, message_id, first_message))


class FakeAnswerService:
    """返回固定证据与可配置的回答流。"""

    def __init__(
        self, *, fail: bool = False, empty: bool = False, related: bool = True
    ) -> None:
        self.fail = fail
        self.empty = empty
        self.related = related
        self.prepare_calls: list[tuple[SessionPrincipal, str, str]] = []

    async def prepare(
        self, principal: SessionPrincipal, conversation_id: str, *, message_id: str
    ) -> PreparedChatAnswer:
        self.prepare_calls.append((principal, conversation_id, message_id))
        intent = (
            IntentRecognitionResult(
                related=True,
                intent="其他",
                entities=IntentEntities(),
                reasoning="测试意图。",
            )
            if self.related
            else IntentRecognitionResult(
                related=False,
                intent="闲聊与通用问答",
                entities=IntentEntities(),
                reasoning="非汽车问题。",
            )
        )
        return PreparedChatAnswer(
            conversation_id=conversation_id,
            user_message_id=message_id,
            question="我的保险什么时候到期？",
            rewritten_question="我的保险什么时候到期？",
            evidence=[
                KnowledgeRetrievalResult(
                    "chunk-1",
                    "保险到期日：2026-12-31",
                    0.9,
                    7,
                    "knowledge_base",
                )
            ]
            if self.related
            else [],
            intent=intent,
            route=QueryRouteResult(
                intent="车主保险",
                strategies=["relational_db", "knowledge_base"],
                reasoning="测试路由。",
                confidence=0.9,
            )
            if self.related
            else None,
            rerank_status="APPLIED" if self.related else "SKIPPED",
        )

    async def stream(self, prepared: PreparedChatAnswer, trace_id: str):
        if self.fail:
            yield make_event("error", {"code": "X", "message": "模型流式回答失败"})
            yield make_event("done", {"status": "error"}, sequence=2)
            return
        if not self.empty:
            yield make_event("answer_delta", {"delta": "保险到期日为"})
            yield make_event("answer_delta", {"delta": "2026-12-31。"}, sequence=2)
        yield make_event("citation", {"citation": {"citation_id": "citation-1"}}, sequence=3)
        yield make_event("done", {"status": "completed"}, sequence=4)


def test_build_principal_maps_customer_and_staff(load_script) -> None:
    module = load_script("collect_live_rag_results.py")

    customer = module.build_principal(
        make_sample(), customer_id="1", staff_id="EVAL-STAFF"
    )
    staff = module.build_principal(
        make_sample(principal_type="STAFF"),
        customer_id="1",
        staff_id="EVAL-STAFF",
        staff_job="leader",
    )

    assert customer == SessionPrincipal(principal_type="CUSTOMER", principal_id="1")
    assert staff == SessionPrincipal(
        principal_type="STAFF", principal_id="EVAL-STAFF", job="leader"
    )
    with pytest.raises(ValueError):
        module.build_principal(
            make_sample(principal_type="GUEST"), customer_id="1", staff_id="EVAL-STAFF"
        )


@pytest.mark.asyncio
async def test_collect_sample_records_authorized_contexts_and_answer(load_script) -> None:
    module = load_script("collect_live_rag_results.py")
    repository = FakeConversationRepository()
    service = FakeAnswerService()
    sample = make_sample()

    row = await module.collect_sample(
        sample,
        service=service,
        repository=repository,
        principal=SessionPrincipal(principal_type="CUSTOMER", principal_id="1"),
        mode="current",
        timeout_seconds=30,
    )

    assert row["id"] == "p0-001"
    assert row["mode"] == "current"
    assert row["status"] == "OK"
    assert row["retrieved_contexts"] == ["保险到期日：2026-12-31"]
    assert row["response"] == "保险到期日为2026-12-31。"
    assert row["sources"] == ["knowledge_base"]
    assert row["metadata"]["rerank_status"] == "APPLIED"
    assert row["metadata"]["strategies"] == ["relational_db", "knowledge_base"]
    assert row["metadata"]["citation_count"] == 1
    assert repository.created[0][0].startswith("eval-")
    assert repository.created[0][1] == module.conversation_owner_id(
        SessionPrincipal(principal_type="CUSTOMER", principal_id="1")
    )


@pytest.mark.asyncio
async def test_collect_sample_discards_partial_answer_on_model_error(load_script) -> None:
    module = load_script("collect_live_rag_results.py")

    row = await module.collect_sample(
        make_sample(),
        service=FakeAnswerService(fail=True),
        repository=FakeConversationRepository(),
        principal=SessionPrincipal(principal_type="CUSTOMER", principal_id="1"),
        mode="current",
        timeout_seconds=30,
    )

    assert row["status"] == "ERROR"
    assert row["response"] == ""
    assert row["error"] == "模型流式回答失败"


@pytest.mark.asyncio
async def test_collect_sample_marks_unrelated_question_skipped(load_script) -> None:
    module = load_script("collect_live_rag_results.py")

    row = await module.collect_sample(
        make_sample(sample_id="p0-020", related=False),
        service=FakeAnswerService(related=False),
        repository=FakeConversationRepository(),
        principal=SessionPrincipal(principal_type="CUSTOMER", principal_id="1"),
        mode="current",
        timeout_seconds=30,
    )

    assert row["status"] == "SKIPPED"
    assert row["retrieved_contexts"] == []
    assert row["response"]


def test_build_eval_row_keeps_ragas_contract_fields(load_script) -> None:
    module = load_script("collect_live_rag_results.py")

    row = module.build_eval_row(
        make_sample(),
        mode="current",
        response="回答",
        contexts=["证据"],
        sources=["knowledge_base"],
        latency_ms=12.5,
        status="OK",
        error=None,
        metadata={"intent": "其他"},
    )

    assert set(row) == {
        "id",
        "mode",
        "retrieved_contexts",
        "response",
        "sources",
        "latency_ms",
        "status",
        "error",
        "metadata",
    }


def test_load_completed_keys_only_counts_finished_rows(load_script, tmp_path: Path) -> None:
    module = load_script("collect_live_rag_results.py")
    path = tmp_path / "runtime-results.jsonl"
    rows = [
        {"id": "p0-001", "mode": "current", "status": "OK"},
        {"id": "p0-002", "mode": "current", "status": "SKIPPED"},
        {"id": "p0-003", "mode": "current", "status": "ERROR"},
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    completed = module.load_completed_keys(path)

    assert completed == {"p0-001::current", "p0-002::current"}


def test_load_samples_rejects_rows_without_required_fields(load_script, tmp_path: Path) -> None:
    module = load_script("collect_live_rag_results.py")
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id": "p0-001"}\n', encoding="utf-8")

    with pytest.raises(ValueError):
        module.load_samples(path)
