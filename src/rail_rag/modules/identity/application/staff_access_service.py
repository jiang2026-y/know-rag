"""员工访问应用服务：编排幂等更新、审计和 Redis Session 撤销。

定义 StaffAccessService 和 StaffAccessUpdateResult；服务负责在 MySQL 更新后同步撤销目标员工的所有 Session。
"""

from dataclasses import asdict, dataclass

from rail_rag.modules.identity.domain.staff_access import StaffAccessState, StaffAccessUpdate
from rail_rag.modules.identity.infrastructure.session_service import RedisSessionService
from rail_rag.modules.identity.infrastructure.staff_access_repository import (
    IdempotencyReplay,
    StaffAccessRepository,
)


@dataclass(frozen=True)
class StaffAccessUpdateResult:
    """管理员接口返回的已提交员工访问状态。"""

    state: StaffAccessState
    revoked_sessions: int

    def as_payload(self) -> dict[str, object]:
        """转换为可持久化的幂等响应数据。"""
        return {"staff": asdict(self.state), "revoked_sessions": self.revoked_sessions}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "StaffAccessUpdateResult":
        """从首次幂等响应恢复结果。"""
        staff = payload["staff"]
        if not isinstance(staff, dict) or not isinstance(payload["revoked_sessions"], int):
            raise ValueError("Idempotency response is invalid")
        return cls(
            state=StaffAccessState(**staff),
            revoked_sessions=payload["revoked_sessions"],
        )


class StaffAccessService:
    """仅供已鉴权管理员调用的员工访问更新用例。"""

    def __init__(self, repository: StaffAccessRepository, sessions: RedisSessionService) -> None:
        self._repository = repository
        self._sessions = sessions

    async def update_staff_access(
        self,
        actor_emp_id: str,
        target_emp_id: str,
        update: StaffAccessUpdate,
        idempotency_key: str,
        trace_id: str,
    ) -> StaffAccessUpdateResult:
        """更新员工权限、写入审计并撤销目标员工已有 Session。"""
        normalized_target = target_emp_id.strip()
        normalized_key = idempotency_key.strip()
        if not normalized_target or not normalized_key or not update.has_changes():
            raise ValueError("Employee ID, idempotency key and an access change are required")
        digest = self._repository.request_digest(normalized_target, update)
        replay = await self._repository.claim_idempotency(actor_emp_id, normalized_key, digest)
        if replay is not None:
            return StaffAccessUpdateResult.from_payload(replay.payload)
        try:
            state = await self._repository.update_staff_access(
                actor_emp_id, normalized_target, update, trace_id
            )
            revoked_sessions = await self._sessions.revoke_staff_sessions(normalized_target)
            result = StaffAccessUpdateResult(state, revoked_sessions)
            await self._repository.complete_idempotency(
                actor_emp_id, normalized_key, result.as_payload()
            )
            return result
        except Exception:
            await self._repository.release_idempotency(actor_emp_id, normalized_key)
            raise
