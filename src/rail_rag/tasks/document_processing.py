"""文档可靠任务：定义文档处理、死信清理与排队任务补发三个 Relier Worker 入口。"""

from relier import TaskContext, rl_task


@rl_task(
    queue="default",
    idempotent=True,
    idempotency_ttl=86400,
    soft_timeout=900,
    hard_timeout=1200,
    name="rail_rag.tasks.document_processing.process_document_task",
)
async def process_document_task(
    document_task_id: str, delivery_attempt: int, ctx: TaskContext
) -> dict[str, str | int]:
    """执行一次文档处理；异常时安排自动重试，耗尽后抛回 Relier 写入 DLQ。"""
    from rail_rag.bootstrap import get_document_task_service

    service = get_document_task_service()
    try:
        result = await service.process_queued_task(document_task_id)
        return {**result, "delivery_attempt": delivery_attempt, "relier_task_id": ctx.task_id}
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        if await service.schedule_retry_or_dead_letter(document_task_id, message):
            return {"status": "retry_scheduled", "delivery_attempt": delivery_attempt}
        raise


@rl_task(
    queue="low_priority",
    idempotent=True,
    idempotency_ttl=3600,
    soft_timeout=60,
    hard_timeout=90,
    name="rail_rag.tasks.document_processing.purge_expired_dead_letters",
)
async def purge_expired_dead_letters(ctx: TaskContext) -> dict[str, int | str]:
    """由 Celery Beat 每小时调用，清理超过配置保留期的死信记录。"""
    from rail_rag.bootstrap import get_task_dead_letter_service

    removed = await get_task_dead_letter_service().purge_expired()
    return {"status": "completed", "removed": removed, "relier_task_id": ctx.task_id}


@rl_task(
    queue="low_priority",
    idempotent=True,
    idempotency_ttl=30,
    soft_timeout=60,
    hard_timeout=90,
    name="rail_rag.tasks.document_processing.redeliver_queued_document_tasks",
)
async def redeliver_queued_document_tasks(ctx: TaskContext) -> dict[str, int | str]:
    """由 Celery Beat 定期补发 MySQL 中仍处于 queued 状态的文档任务。"""
    from rail_rag.bootstrap import get_document_task_service

    delivered = await get_document_task_service().redeliver_queued_tasks()
    return {"status": "completed", "delivered": delivered, "relier_task_id": ctx.task_id}
