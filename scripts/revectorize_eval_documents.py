"""评测文档重新向量化脚本：把已分片的 P0 测试文档重新写入当前 Elasticsearch。

定义 EvalDocument、InProcessEvaluationLock、load_eval_documents、reset_document、
revectorize 和 main；脚本默认只预览，显式传入 --apply 才会重置状态并调用审核后的向量化链路。
用于 Elasticsearch 索引被清空或切换到隔离评测集群之后的重建；一次性维护使用进程内互斥，
不依赖 Redis 或应用实例的运行状态。
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pymysql

from rail_rag.config.settings import Settings, get_settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.document_review_service import (
    DocumentReviewService,
)
from rail_rag.modules.knowledge.application.document_vectorization_service import (
    DocumentVectorizationService,
)
from rail_rag.modules.knowledge.domain.document_access import DOCUMENT_ACCESS_LABELS
from rail_rag.modules.knowledge.infrastructure.dashscope_embedding_client import (
    DashScopeEmbeddingClient,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    ElasticsearchEmbeddingStore,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    KnowledgeSegmentRepository,
)
DEFAULT_TITLE_PREFIX = "P0测试-"
SEED_REVIEWER = SessionPrincipal(
    principal_type="STAFF", principal_id="LOCAL-EVALUATION-SEED", job="leader"
)


@dataclass(frozen=True)
class EvalDocument:
    """需要重新向量化的评测文档及其原有可见标签。"""

    document_id: int
    version_id: int
    title: str
    accessible_by: str


class InProcessEvaluationLock:
    """维护脚本专用的进程内互斥锁，避免一次性重建额外依赖 Redis。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, scene: str, key: object, *, wait_time_seconds: float = 0):
        """按场景与键获取进程内互斥锁并自动释放。"""
        lock = self._locks.setdefault(f"{scene}:{key}", asyncio.Lock())
        async with lock:
            yield


def _connect(settings: Settings) -> pymysql.Connection:
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password.get_secret_value(),
        database=settings.mysql_database,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=settings.mysql_connect_timeout_seconds,
    )


def load_eval_documents(
    settings: Settings, *, title_prefix: str = DEFAULT_TITLE_PREFIX
) -> list[EvalDocument]:
    """读取指定前缀的未删除文档，并校验版本与可见标签。"""
    with _connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT doc_id, current_version_id, doc_title, accessible_by
                FROM knowledge_document
                WHERE doc_title LIKE %s AND deleted = 0
                ORDER BY doc_id
                """,
                (f"{title_prefix}%",),
            )
            rows = cursor.fetchall()
    documents: list[EvalDocument] = []
    for document_id, version_id, title, accessible_by in rows:
        if version_id is None:
            raise ValueError(f"文档 {document_id} 没有当前版本")
        if accessible_by not in DOCUMENT_ACCESS_LABELS:
            raise ValueError(f"文档 {document_id} 的可见标签无效：{accessible_by}")
        documents.append(
            EvalDocument(int(document_id), int(version_id), str(title), str(accessible_by))
        )
    return documents


def reset_document(settings: Settings, document: EvalDocument) -> None:
    """把文档与分片恢复到可重新审核、待向量化的状态。"""
    with _connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE knowledge_document SET status = %s WHERE doc_id = %s AND deleted = 0",
                ("CHUNKED", document.document_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"文档 {document.document_id} 不存在或已删除")
            cursor.execute(
                """
                UPDATE knowledge_segment
                SET status = %s, embedding_id = NULL
                WHERE document_id = %s AND document_version = %s AND deleted = 0
                """,
                ("INIT", document.document_id, document.version_id),
            )
        connection.commit()


async def revectorize(
    settings: Settings, documents: list[EvalDocument], *, apply: bool
) -> list[str]:
    """逐篇重置并重新向量化；单篇失败不影响其他文档。"""
    if not apply:
        return [
            f"{document.title}（doc_id={document.document_id}）：将重置并重新向量化"
            for document in documents
        ]
    document_repository = KnowledgeDocumentRepository(settings)
    vectorization_service = DocumentVectorizationService(
        document_repository,
        KnowledgeSegmentRepository(settings),
        ElasticsearchEmbeddingStore(settings, embedder=DashScopeEmbeddingClient(settings)),
        InProcessEvaluationLock(),  # type: ignore[arg-type]
    )
    review_service = DocumentReviewService(document_repository, vectorization_service)
    messages: list[str] = []
    for document in documents:
        try:
            reset_document(settings, document)
            result = await review_service.approve(
                SEED_REVIEWER,
                document.document_id,
                document.accessible_by,  # type: ignore[arg-type]
            )
            messages.append(
                f"{document.title}：{result.status}（可见标签 {document.accessible_by}）"
            )
        except Exception as exc:
            messages.append(
                f"{document.title}：失败 {type(exc).__name__}: {exc}"
            )
    return messages


def main() -> None:
    """解析参数并执行预览或重新向量化。"""
    parser = argparse.ArgumentParser(description="重新向量化 P0 评测文档")
    parser.add_argument("--title-prefix", default=DEFAULT_TITLE_PREFIX)
    parser.add_argument(
        "--apply", action="store_true", help="确认重置状态并调用向量化服务"
    )
    args = parser.parse_args()

    settings = get_settings()
    try:
        documents = load_eval_documents(settings, title_prefix=args.title_prefix)
    except Exception as exc:
        raise SystemExit(f"读取评测文档失败：{type(exc).__name__}: {exc}") from exc
    if not documents:
        raise SystemExit(
            f"没有找到标题以 {args.title_prefix} 开头的评测文档，请先运行 seed_rag_evaluation_data.py"
        )
    print(f"目标 Elasticsearch：{settings.elasticsearch_host}；索引：{settings.elasticsearch_index}")
    print(f"待处理文档：{len(documents)} 篇（{'写入模式' if args.apply else '预览模式'}）")
    messages = asyncio.run(revectorize(settings, documents, apply=args.apply))
    for message in messages:
        print(message)
    failures = [message for message in messages if "失败" in message]
    if failures:
        raise SystemExit(f"有 {len(failures)} 篇文档重新向量化失败")
    if not args.apply:
        print("预览完成；确认后追加 --apply 执行。")


if __name__ == "__main__":
    main()
