"""Relier 文档任务调度器：定义 dispatch，将 MySQL 任务标识投递到可靠 Celery Worker。"""


class RelierDocumentTaskDispatcher:
    """使用 Relier 异步投递文档处理任务，并让交付轮次参与 Redis Lua 幂等键。"""

    async def dispatch(self, task_id: str, delivery_attempt: int) -> str:
        """投递任务，返回独立的 Celery/Relier 运行时任务 ID。"""
        # 延迟导入避免 FastAPI 的依赖组装与 Worker 任务注册形成循环导入。
        from rail_rag.tasks.celery_app import celery_app  # noqa: F401
        from rail_rag.tasks.document_processing import process_document_task

        receipt = await process_document_task.apush(task_id, delivery_attempt)
        return receipt.id
