"""流式问答应用服务：编排会话归属、多轮记忆、受控检索、qwen3.7-plus 与 SSE 领域事件。

定义 ChatAnswerService、ConversationNotFoundError、PreparedChatAnswer；服务负责准备可信问题、
按窗口读取最近对话历史、构造仅含授权证据的 Prompt、流式输出查询改写与回答事件，
并仅在完整成功后保存助手消息和引用。
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Protocol
from uuid import uuid4

from rail_rag.config.settings import Settings
from rail_rag.modules.conversation.domain.stream_event import ChatStreamEvent
from rail_rag.modules.conversation.infrastructure.conversation_repository import (
    ChatConversationRepository,
    StoredConversationMessage,
    StoredUserMessage,
)
from rail_rag.modules.conversation.infrastructure.dashscope_answer_client import (
    AnswerClient,
)
from rail_rag.modules.identity.domain.principal import SessionPrincipal, conversation_owner_id
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalResult,
    KnowledgeRetrievalService,
)
from rail_rag.modules.qa.application.query_rewrite_service import QueryRewriteService
from rail_rag.modules.qa.application.query_data_source_service import QueryDataSourceService
from rail_rag.modules.qa.application.query_router_service import QueryRouterService
from rail_rag.modules.qa.application.rerank_service import RerankService
from rail_rag.modules.qa.domain.query_rewrite import QueryRewriteResult
from rail_rag.modules.qa.domain.query_route import QueryRouteResult


logger = logging.getLogger(__name__)


class IntentDecision(Protocol):
    """定义会话编排需要的最小意图决策，只暴露是否允许检索。"""

    related: bool


class IntentRecognizer(Protocol):
    """定义会话模块反向隔离的意图识别端口。"""

    async def recognize(self, principal: SessionPrincipal, question: str) -> IntentDecision:
        """返回服务端可安全使用的意图路由决策。"""


class ConversationNotFoundError(LookupError):
    """会话不属于当前主体、已关闭或指定用户消息不存在时抛出。"""


@dataclass(frozen=True)
class PreparedChatAnswer:
    """已完成归属校验和权限检索、可安全送往模型的回答输入。"""

    conversation_id: str
    user_message_id: str
    question: str
    rewritten_question: str
    evidence: list[KnowledgeRetrievalResult]
    intent: IntentDecision
    rewrite_status: str = "SKIPPED"
    route: QueryRouteResult | None = None
    enhanced_query: dict[str, object] = field(default_factory=dict)
    metadata_task: asyncio.Task[None] | None = None
    retrieval_metadata_task: asyncio.Task[None] | None = None
    rerank_status: str = "SKIPPED"
    history: list[StoredConversationMessage] = field(default_factory=list)


class ChatAnswerService:
    """生成受控知识问答 SSE 事件，并在成功完成后持久化回答。"""

    _PROMPT_VERSION = "p0-rag-v1"
    _SYSTEM_PROMPT = (
        "你是汽车企业知识问答助手。只能依据提供的已授权知识证据回答；"
        "证据不足时必须明确说明，不得编造车型、政策、车辆或内部流程。"
        "不要输出思维过程、权限标签、原始分片 ID 或虚构链接。"
    )
    _GENERAL_SYSTEM_PROMPT = (
        "你是汽车企业的通用对话助手。当前问题不应访问企业内部知识或用户业务数据；"
        "请基于一般常识礼貌、简洁地回答。涉及高风险、实时政策或无法确认的事实时，应明确说明需要官方渠道确认。"
        "不要输出思维过程、权限标签、内部系统信息或虚构链接。"
    )

    def __init__(
        self,
        settings: Settings,
        repository: ChatConversationRepository,
        retrieval_service: KnowledgeRetrievalService,
        answer_client: AnswerClient,
        intent_service: IntentRecognizer,
        query_rewrite_service: QueryRewriteService | None = None,
        query_router_service: QueryRouterService | None = None,
        data_source_service: QueryDataSourceService | None = None,
        rerank_service: RerankService | None = None,
    ) -> None:
        self._repository = repository
        self._retrieval_service = retrieval_service
        self._answer_client = answer_client
        self._intent_service = intent_service
        self._query_rewrite_service = query_rewrite_service
        self._query_router_service = query_router_service
        self._data_source_service = data_source_service
        self._rerank_service = rerank_service
        self._model_name = settings.chat_answer_model_name
        self._top_k = settings.chat_rag_top_k
        self._candidate_limit = (
            settings.chat_rag_vector_top_k + settings.chat_rag_full_text_top_k
            if rerank_service is not None
            else self._top_k
        )
        self._context_max_characters = settings.chat_rag_context_max_characters
        self._heartbeat_seconds = settings.chat_sse_heartbeat_seconds
        self._history_max_messages = settings.chat_history_max_messages
        self._history_max_characters = settings.chat_history_max_characters

    async def prepare(
        self,
        principal: SessionPrincipal,
        conversation_id: str,
        *,
        message_id: str | None = None,
        message: str | None = None,
    ) -> PreparedChatAnswer:
        """校验会话归属，取得或创建用户问题，再执行服务端权限检索。"""
        owner_id = conversation_owner_id(principal)
        user_message = await self._resolve_user_message(
            conversation_id, owner_id, message_id=message_id, message=message
        )
        history = await self._load_history(
            conversation_id, owner_id, user_message.message_id
        )
        intent = await self._intent_service.recognize(principal, user_message.content)
        rewrite = (
            QueryRewriteResult(
                original_query=user_message.content,
                rewritten_query=user_message.content,
                status="SKIPPED",
            )
            if self._query_rewrite_service is None
            else await self._query_rewrite_service.rewrite(
                user_message.content, related=intent.related
            )
        )
        route = (
            await self._query_router_service.route(user_message.content)
            if intent.related and self._query_router_service is not None
            else None
        )
        enhanced_query = {
            "query": rewrite.rewritten_query,
            "context": {
                "principal_id_hash": conversation_owner_id(principal),
                "current_time": datetime.now(timezone.utc).isoformat(),
            },
        }
        metadata_task: asyncio.Task[None] | None = None
        update_metadata = getattr(self._repository, "update_user_message_metadata", None)
        if update_metadata is not None:
            intent_status = getattr(intent, "status", "SUCCESS")
            intent_attempts = getattr(intent, "attempts", 1)
            intent_error_type = getattr(intent, "error_type", None)
            metadata_task = asyncio.create_task(
                update_metadata(
                    conversation_id,
                    user_message.message_id,
                    {
                        "intentRecognition": {
                            "status": intent_status,
                            "attempts": intent_attempts,
                            "error_type": intent_error_type,
                            "related": intent.related,
                            "intent": intent.intent,
                        },
                        "queryRewrite": rewrite.model_dump(mode="json"),
                        "enhancedQuery": enhanced_query,
                        "queryRoute": route.model_dump(mode="json") if route is not None else {"status": "EMPTY"},
                    },
                )
            )
        retrieval_fallbacks: list[dict[str, str]] = []
        if not intent.related:
            evidence = []
        elif self._query_router_service is None or self._data_source_service is None:
            evidence = await self._retrieval_service.search(
                principal, rewrite.rewritten_query, limit=self._candidate_limit
            )
        elif route is None:
            evidence = []
        else:
            strategies = getattr(route, "strategies", [route.strategy])
            evidence, retrieval_fallbacks = await self._retrieve_evidence(
                principal, strategies, rewrite.rewritten_query
            )
        rerank_status = "SKIPPED"
        retrieval_metadata_task: asyncio.Task[None] | None = None
        if intent.related and self._rerank_service is not None:
            outcome = await self._rerank_service.rerank(rewrite.rewritten_query, evidence)
            evidence = outcome.evidence
            rerank_status = outcome.status
            rerank_audit: dict[str, Any] = outcome.audit
        else:
            rerank_audit = {}
        if update_metadata is not None and (rerank_audit or retrieval_fallbacks):
            retrieval_metadata_task = asyncio.create_task(
                update_metadata(
                    conversation_id,
                    user_message.message_id,
                    {
                        "retrieval": {
                            "rerank_status": rerank_status,
                            "fallbacks": retrieval_fallbacks,
                            **rerank_audit,
                        }
                    },
                )
            )
        return PreparedChatAnswer(
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            question=user_message.content,
            rewritten_question=rewrite.rewritten_query,
            evidence=evidence,
            intent=intent,
            rewrite_status=rewrite.status,
            route=route,
            enhanced_query=enhanced_query,
            metadata_task=metadata_task,
            retrieval_metadata_task=retrieval_metadata_task,
            rerank_status=rerank_status,
            history=history,
        )

    async def stream(
        self, prepared: PreparedChatAnswer, trace_id: str, *, initial_sequence: int = 0
    ) -> AsyncIterator[ChatStreamEvent | None]:
        """生成回答、引用、错误和完成事件；None 表示需要发送 SSE 心跳。"""
        prompt = self._user_prompt(
            prepared.question,
            prepared.evidence,
            prepared.intent.related,
            prepared.history,
        )
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue(maxsize=100)
        producer = asyncio.create_task(
            self._produce_deltas(
                self._SYSTEM_PROMPT if prepared.intent.related else self._GENERAL_SYSTEM_PROMPT,
                prompt,
                queue,
            )
        )
        sequence = initial_sequence
        answer_parts: list[str] = []
        try:
            sequence += 1
            yield self._event(
                "query_rewrite",
                prepared,
                sequence,
                trace_id,
                {
                    "original_query": prepared.question,
                    "rewritten_query": prepared.rewritten_question,
                    "status": prepared.rewrite_status,
                },
            )
            while True:
                try:
                    kind, value = await asyncio.wait_for(
                        queue.get(), timeout=self._heartbeat_seconds
                    )
                except TimeoutError:
                    yield None
                    continue
                if kind == "delta":
                    delta = value
                    if not isinstance(delta, str):
                        raise RuntimeError("Model stream delta is invalid")
                    answer_parts.append(delta)
                    sequence += 1
                    yield self._event(
                        "answer_delta",
                        prepared,
                        sequence,
                        trace_id,
                        {"delta": delta},
                    )
                    continue
                if kind == "error":
                    raise value if isinstance(value, Exception) else RuntimeError("Model stream failed")
                if kind == "completed":
                    break

            answer = "".join(answer_parts).strip()
            if not answer:
                raise RuntimeError("Model returned an empty answer")
            await self._complete_metadata_task(prepared)
            citations = self._citations(prepared.evidence)
            for citation in citations:
                sequence += 1
                yield self._event(
                    "citation",
                    prepared,
                    sequence,
                    trace_id,
                    {"citation": self._public_citation(citation)},
                )
            answer_message_id = str(uuid4())
            await self._repository.append_assistant_message(
                prepared.conversation_id,
                answer_message_id,
                answer,
                self._model_name,
                citations,
                prepared.user_message_id,
            )
            sequence += 1
            yield self._event(
                "done",
                prepared,
                sequence,
                trace_id,
                {"status": "completed", "answer_message_id": answer_message_id},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            sequence += 1
            yield self._event(
                "error",
                prepared,
                sequence,
                trace_id,
                {
                    "code": "CHAT_ANSWER_UNAVAILABLE",
                    "message": "暂时无法生成回答，请稍后重试。",
                },
            )
            sequence += 1
            yield self._event(
                "done",
                prepared,
                sequence,
                trace_id,
                {"status": "error", "answer_message_id": None},
            )
        finally:
            await self._complete_metadata_task(prepared)
            producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                pass

    @staticmethod
    async def _complete_metadata_task(prepared: PreparedChatAnswer) -> None:
        """等待查询审计后台写入完成，但不让其故障阻断回答。"""
        if prepared.metadata_task is not None and not prepared.metadata_task.done():
            await asyncio.gather(prepared.metadata_task, return_exceptions=True)
        if (
            prepared.retrieval_metadata_task is not None
            and not prepared.retrieval_metadata_task.done()
        ):
            await asyncio.gather(prepared.retrieval_metadata_task, return_exceptions=True)

    async def _resolve_user_message(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        message_id: str | None,
        message: str | None,
    ) -> StoredUserMessage:
        if message_id is not None:
            user_message = await self._repository.get_owned_user_message(
                conversation_id, owner_id, message_id
            )
        elif message is not None:
            user_message = await self._repository.create_user_message(
                conversation_id, owner_id, str(uuid4()), message.strip()
            )
        else:
            raise ValueError("Message source is required")
        if user_message is None:
            raise ConversationNotFoundError("Conversation or user message was not found")
        return user_message

    async def _retrieve_evidence(
        self,
        principal: SessionPrincipal,
        strategies: list[str],
        question: str,
    ) -> tuple[list[KnowledgeRetrievalResult], list[dict[str, str]]]:
        """执行多路受控检索并返回结构化源回落审计；替身只需实现 retrieve_many。"""
        retrieval_with_audit = getattr(
            self._data_source_service, "retrieve_many_with_audit", None
        )
        if retrieval_with_audit is None:
            evidence = await self._data_source_service.retrieve_many(
                principal, strategies, question, limit=self._candidate_limit
            )
            return evidence, []
        outcome = await retrieval_with_audit(
            principal, strategies, question, limit=self._candidate_limit
        )
        return outcome.evidence, outcome.fallbacks

    async def _produce_deltas(
        self, system_prompt: str, prompt: str, queue: asyncio.Queue[tuple[str, object]]
    ) -> None:
        """独立消费上游模型流，使下游可在等待增量期间发送心跳。"""
        try:
            async for delta in self._answer_client.stream_answer(system_prompt, prompt):
                await queue.put(("delta", delta))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(("error", exc))
        finally:
            await queue.put(("completed", None))

    def _user_prompt(
        self,
        question: str,
        evidence: list[KnowledgeRetrievalResult],
        related: bool,
        history: list[StoredConversationMessage],
    ) -> str:
        """在固定字符预算内构造含最近对话历史、且只含已授权分片的用户提示。"""
        sections: list[str] = []
        history_section = self._history_section(history)
        if history_section:
            sections.append(f"历史对话：\n{history_section}")
        sections.append(f"用户问题：\n{question}")
        if not related:
            return "\n\n".join(sections)
        remaining = self._context_max_characters
        evidence_sections: list[str] = []
        for index, item in enumerate(evidence, start=1):
            source = item.document_id if item.source_type == "knowledge_base" else item.source_type
            header = f"[证据 {index}] 来源={source or '未知'}\n"
            available = remaining - len(header)
            if available <= 0:
                break
            text = item.text[:available]
            evidence_sections.append(f"{header}{text}")
            remaining -= len(header) + len(text)
        context = (
            "\n\n".join(evidence_sections)
            if evidence_sections
            else "（没有可用的已授权知识证据）"
        )
        sections.append(f"已授权知识证据：\n{context}")
        return "\n\n".join(sections)

    def _history_section(self, history: list[StoredConversationMessage]) -> str:
        """按字符预算从最近往前保留历史消息，返回可直接拼接的历史对话片段。"""
        if not history or self._history_max_characters <= 0:
            return ""
        budget = self._history_max_characters
        lines: list[str] = []
        for message in reversed(history):
            content = message.content.strip()
            if not content:
                continue
            role = "用户" if message.role == "user" else "助手"
            line = f"{role}：{content}"
            if len(line) > budget:
                break
            lines.append(line)
            budget -= len(line)
        if not lines:
            return ""
        lines.reverse()
        return "\n".join(lines)

    async def _load_history(
        self, conversation_id: str, owner_id: str, current_message_id: str
    ) -> list[StoredConversationMessage]:
        """读取最近若干条已持久化消息；读取失败只降级为无历史。"""
        if self._history_max_messages <= 0:
            return []
        loader = getattr(self._repository, "list_recent_messages", None)
        if loader is None:
            return []
        try:
            return await loader(
                conversation_id,
                owner_id,
                self._history_max_messages,
                exclude_message_id=current_message_id,
            )
        except Exception as exc:  # 历史读取失败不得阻断回答主链路。
            logger.warning(
                "Chat history load failed; continuing without history.",
                extra={
                    "conversation_id": conversation_id,
                    "error_type": type(exc).__name__,
                },
            )
            return []

    @staticmethod
    def _citations(evidence: list[KnowledgeRetrievalResult]) -> list[dict[str, Any]]:
        """将已参与模型上下文的分片转换为稳定、可审计的引用列表。"""
        citations: list[dict[str, Any]] = []
        for index, item in enumerate(evidence, start=1):
            citation: dict[str, Any] = {"citation_id": f"citation-{index}"}
            if item.source_type == "knowledge_base":
                citation.update(
                    {
                        "document_id": item.document_id,
                        "chunk_id": item.chunk_id,
                        "source_name": item.document_title or "企业知识库文档",
                        "section": item.section or None,
                        "source_type": item.source_type,
                    }
                )
            else:
                citation.update(
                    {
                        "source_type": item.source_type,
                        "source_id": item.source_id,
                        "source_name": (
                            "我的车辆信息"
                            if item.source_type == "relational_db"
                            else "公开车型图谱"
                        ),
                        "section": None,
                    }
                )
            citations.append(citation)
        return citations

    @staticmethod
    def _public_citation(citation: dict[str, Any]) -> dict[str, Any]:
        """仅向浏览器返回可读来源信息，内部文档和分片 ID 留在后端审计记录中。"""
        return {
            key: citation[key]
            for key in ("citation_id", "source_name", "section", "source_type")
            if key in citation and citation[key] is not None
        }

    @staticmethod
    def _event(
        event: str,
        prepared: PreparedChatAnswer,
        sequence: int,
        trace_id: str,
        payload: dict[str, Any],
    ) -> ChatStreamEvent:
        """创建带统一会话和追踪字段的 SSE 领域事件。"""
        return ChatStreamEvent(
            event=event,  # type: ignore[arg-type]
            conversation_id=prepared.conversation_id,
            message_id=prepared.user_message_id,
            sequence=sequence,
            trace_id=trace_id,
            payload=payload,
        )
