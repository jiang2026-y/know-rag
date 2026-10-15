"""任务死信服务：定义死信列表、七天清理和仅受控文档任务的人工重放准备。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from relier.core.dlq import DeadLetterQueue
from relier.storage.redis import get_relier_redis

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.application.document_task_service import DocumentTaskService


class TaskDeadLetterNotFoundError(LookupError):
    """请求的 Relier 死信任务不存在或已超过保留期时抛出。"""


class TaskDeadLetterReplayNotAllowedError(ValueError):
    """死信记录不属于允许人工重放的项目文档任务时抛出。"""


@dataclass(frozen=True)
class DeadLetterTaskSummary:
    """管理端展示死信任务所需的最小、非敏感元数据。"""

    relier_task_id: str
    task_name: str
    reason: str
    resurrections: int
    quarantined_at: datetime
    document_task_id: str | None


class TaskDeadLetterService:
    """封装 Relier DLQ 的到期清理与项目级人工重放约束。"""

    _DOCUMENT_TASK_NAME = "rail_rag.tasks.document_processing.process_document_task"

    def __init__(self, settings: Settings, document_tasks: DocumentTaskService) -> None:
        self._settings = settings
        self._document_tasks = document_tasks

    async def list_entries(self, limit: int = 100) -> list[DeadLetterTaskSummary]:
        """清除过期记录后返回最近的死信任务摘要。"""
        await self.purge_expired()
        entries = await DeadLetterQueue.list_tasks(limit=limit)
        summaries = [self._summary(entry) for entry in entries]
        return [summary for summary in summaries if summary is not None]

    async def replay_document_task(self, relier_task_id: str) -> None:
        """重放一条文档处理死信；必须先恢复 MySQL 状态，再删除旧 DLQ 记录。"""
        await self.purge_expired()
        entry = await DeadLetterQueue.inspect(relier_task_id)
        if entry is None:
            raise TaskDeadLetterNotFoundError("Dead-letter task was not found")
        document_task_id = self._document_task_id(entry)
        if document_task_id is None:
            raise TaskDeadLetterReplayNotAllowedError(
                "Only LLMcopy document-processing dead-letter tasks can be replayed"
            )
        replayed = await self._document_tasks.prepare_dead_letter_replay(document_task_id)
        if not replayed:
            raise TaskDeadLetterReplayNotAllowedError(
                "Document task is not in dead-lettered state"
            )
        redis = await get_relier_redis()
        await redis.hdel(DeadLetterQueue.DLQ_HASH_KEY, relier_task_id)

    async def purge_expired(self) -> int:
        """删除超过七天保留期的 DLQ 哈希字段，避免 Redis 中无限累积故障记录。"""
        cutoff = datetime.now(UTC) - timedelta(days=self._settings.task_dlq_retention_days)
        redis = await get_relier_redis()
        removed = 0
        for entry in await DeadLetterQueue.list_tasks():
            relier_task_id = entry.get("task_id")
            quarantined_at = self._parse_datetime(entry.get("quarantined_at"))
            if isinstance(relier_task_id, str) and quarantined_at is not None and quarantined_at < cutoff:
                removed += int(await redis.hdel(DeadLetterQueue.DLQ_HASH_KEY, relier_task_id))
        return removed

    def _summary(self, entry: dict[str, Any]) -> DeadLetterTaskSummary | None:
        relier_task_id = entry.get("task_id")
        quarantined_at = self._parse_datetime(entry.get("quarantined_at"))
        if not isinstance(relier_task_id, str) or quarantined_at is None:
            return None
        task_name = entry.get("task_name")
        reason = entry.get("reason")
        resurrections = entry.get("resurrections")
        return DeadLetterTaskSummary(
            relier_task_id=relier_task_id,
            task_name=task_name if isinstance(task_name, str) else "unknown",
            reason=reason if isinstance(reason, str) else "unknown",
            resurrections=resurrections if isinstance(resurrections, int) else 0,
            quarantined_at=quarantined_at,
            document_task_id=self._document_task_id(entry),
        )

    def _document_task_id(self, entry: dict[str, Any]) -> str | None:
        """从 Relier 原始信封中谨慎提取项目的 MySQL 文档任务 ID。"""
        if entry.get("task_name") != self._DOCUMENT_TASK_NAME:
            return None
        raw_args = entry.get("args")
        if not isinstance(raw_args, list) or not raw_args or not isinstance(raw_args[0], dict):
            return None
        payload = raw_args[0].get("payload")
        if not isinstance(payload, dict):
            return None
        task_args = payload.get("args")
        if not isinstance(task_args, list) or not task_args or not isinstance(task_args[0], str):
            return None
        return task_args[0]

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
