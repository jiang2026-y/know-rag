"""Celery/Relier 应用入口：注入独立任务 Redis 配置、注册任务和周期清理。"""

import os

from rail_rag.config.settings import get_settings


_settings = get_settings()
os.environ.setdefault("RELIER_REDIS_URL", _settings.task_redis_url)
if _settings.task_redis_password is not None:
    os.environ.setdefault(
        "RELIER_REDIS_PASSWORD", _settings.task_redis_password.get_secret_value()
    )
os.environ.setdefault("RELIER_CELERY_WORKER_COUNT", "1")
os.environ.setdefault("RELIER_CELERY_WORKER_CONCURRENCY", "1")
os.environ.setdefault("RELIER_SOFT_TIMEOUT", "900")
os.environ.setdefault("RELIER_HARD_TIMEOUT", "1200")
os.environ.setdefault("RELIER_IDEMPOTENCY_INFLIGHT_TTL", "1210")

from relier.tasks.app import celery_app

from rail_rag.tasks.document_processing import (  # noqa: F401
    process_document_task,
    purge_expired_dead_letters,
    redeliver_queued_document_tasks,
)


celery_app.conf.beat_schedule = {
    "purge-expired-dead-letters-hourly": {
        "task": "rail_rag.tasks.document_processing.purge_expired_dead_letters",
        "schedule": 3600.0,
    },
    "redeliver-queued-document-tasks-minute": {
        "task": "rail_rag.tasks.document_processing.redeliver_queued_document_tasks",
        "schedule": 60.0,
    },
}
