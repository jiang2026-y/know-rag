"""回答赞踩反馈服务测试：验证归属散列、幂等重放和赞踩更新。

定义 FakeFeedbackRepository、员工主体构造函数和两项异步测试；测试确保服务端而非
客户端决定反馈主体与权限快照。
"""

from datetime import datetime

import pytest

from rail_rag.modules.conversation.application.feedback_service import FeedbackService
from rail_rag.modules.conversation.infrastructure.feedback_repository import FeedbackWriteResult
from rail_rag.modules.identity.domain.principal import SessionPrincipal, conversation_owner_id
from rail_rag.modules.identity.infrastructure.staff_access_repository import IdempotencyReplay


class FakeFeedbackRepository:
    """记录反馈应用服务调用的最小仓储替身。"""

    def __init__(self, replay: IdempotencyReplay | None = None) -> None:
        self.replay = replay
        self.claim_arguments: tuple[str, str, str] | None = None
        self.saved: dict[str, object] | None = None
        self.completed: dict[str, object] | None = None
        self.released = False

    @staticmethod
    def request_digest(message_id: str, reaction: str) -> str:
        """返回测试可识别的稳定摘要。"""
        return f"{message_id}:{reaction}"

    async def claim_idempotency(self, actor_id: str, key: str, digest: str) -> IdempotencyReplay | None:
        """记录幂等声明参数。"""
        self.claim_arguments = (actor_id, key, digest)
        return self.replay

    async def save_feedback(self, **kwargs: object) -> FeedbackWriteResult:
        """记录保存参数并返回固定结果。"""
        self.saved = kwargs
        return FeedbackWriteResult("answer-1", "LIKE", datetime(2027, 8, 6))

    async def complete_idempotency(self, actor_id: str, key: str, payload: dict[str, str]) -> None:
        """记录成功响应缓存调用。"""
        self.completed = {"actor_id": actor_id, "key": key, "payload": payload}

    async def release_idempotency(self, *_: str) -> None:
        """记录异常释放调用。"""
        self.released = True


def staff_principal() -> SessionPrincipal:
    """构造携带岗位快照的可信员工主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id="STAFF-1", job="staff", dept_id=8)


@pytest.mark.asyncio
async def test_feedback_uses_server_side_owner_and_permission_snapshot() -> None:
    repository = FakeFeedbackRepository()
    service = FeedbackService(repository)  # type: ignore[arg-type]

    result = await service.submit(staff_principal(), "answer-1", "LIKE", "key-1", "trace-1")

    assert result.reaction == "LIKE"
    assert repository.claim_arguments == (conversation_owner_id(staff_principal()), "key-1", "answer-1:LIKE")
    assert repository.saved == {
        "actor_id": conversation_owner_id(staff_principal()),
        "message_id": "answer-1",
        "reaction": "LIKE",
        "permission_snapshot": {"principal_type": "STAFF", "job": "staff", "dept_id": 8},
        "trace_id": "trace-1",
    }
    assert repository.completed is not None


@pytest.mark.asyncio
async def test_feedback_replay_does_not_save_again() -> None:
    repository = FakeFeedbackRepository(
        IdempotencyReplay(
            {"message_id": "answer-1", "reaction": "DISLIKE", "expires_at": "2027-08-06T00:00:00"}
        )
    )
    service = FeedbackService(repository)  # type: ignore[arg-type]

    result = await service.submit(staff_principal(), "answer-1", "DISLIKE", "key-1", "trace-1")

    assert result.reaction == "DISLIKE"
    assert repository.saved is None
    assert repository.completed is None
