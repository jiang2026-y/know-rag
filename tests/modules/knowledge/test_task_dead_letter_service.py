"""死信服务测试：验证 Relier 信封解析只允许项目文档任务进入人工重放流程。"""

from datetime import UTC, datetime

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.application.task_dead_letter_service import (
    TaskDeadLetterService,
)


def _entry(task_name: str) -> dict[str, object]:
    return {
        "task_id": "relier-task-1",
        "task_name": task_name,
        "reason": "RuntimeError",
        "resurrections": 1,
        "quarantined_at": datetime.now(UTC).isoformat(),
        "args": [
            {
                "payload": {
                    "args": ["document-task-1", 0],
                    "kwargs": {},
                }
            }
        ],
    }


def test_only_document_processing_dead_letters_expose_document_task_id() -> None:
    service = TaskDeadLetterService(Settings(), object())  # type: ignore[arg-type]

    valid = _entry("rail_rag.tasks.document_processing.process_document_task")
    other = _entry("rail_rag.tasks.document_processing.purge_expired_dead_letters")

    assert service._document_task_id(valid) == "document-task-1"
    assert service._document_task_id(other) is None
    assert service._summary(valid) is not None
