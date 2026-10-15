"""回答反馈应用服务：编排归属、幂等与可更新的赞踩保存。

定义 FeedbackService；服务只使用可信 Session 推导会话归属和权限快照，确保用户只能
评价自己已完成的助手回答。
"""

from rail_rag.modules.conversation.domain.feedback import FeedbackReaction
from rail_rag.modules.conversation.infrastructure.feedback_repository import FeedbackRepository, FeedbackWriteResult
from rail_rag.modules.identity.domain.principal import SessionPrincipal, conversation_owner_id


class FeedbackService:
    """保存对已授权助手回答的赞或踩。"""

    def __init__(self, repository: FeedbackRepository) -> None:
        self._repository = repository

    async def submit(self, principal: SessionPrincipal, message_id: str, reaction: FeedbackReaction, idempotency_key: str, trace_id: str) -> FeedbackWriteResult:
        """校验输入、声明幂等键，并创建或更新同一回答的反馈。"""
        normalized_message_id, normalized_key = message_id.strip(), idempotency_key.strip()
        if not normalized_message_id or not normalized_key:
            raise ValueError("Message ID and idempotency key are required")
        actor_id = conversation_owner_id(principal)
        replay = await self._repository.claim_idempotency(actor_id, normalized_key, self._repository.request_digest(normalized_message_id, reaction))
        if replay is not None:
            return FeedbackWriteResult.from_payload(replay.payload)
        try:
            result = await self._repository.save_feedback(actor_id=actor_id, message_id=normalized_message_id, reaction=reaction, permission_snapshot={"principal_type": principal.principal_type, "job": principal.job, "dept_id": principal.dept_id}, trace_id=trace_id)
            await self._repository.complete_idempotency(actor_id, normalized_key, result.as_payload())
            return result
        except Exception:
            await self._repository.release_idempotency(actor_id, normalized_key)
            raise
