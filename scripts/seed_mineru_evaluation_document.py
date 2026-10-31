"""将同源 MinerU 测试文档幂等写入隔离评测库并完成向量化。

定义 EvalDocumentSeed、InProcessEvaluationLock、document_by_title、seed_document 和 main；
脚本默认只预览，传入 --apply 后才会创建文档、切片、审核并调用当前 Embedding 链路。
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.document_review_service import DocumentReviewService
from rail_rag.modules.knowledge.application.document_vectorization_service import (
    DocumentVectorizationService,
)
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    MarkdownParentChildSplitter,
)
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


DEFAULT_TITLE = "MinerU评测-汽车产品手册"
SEED_REVIEWER = SessionPrincipal(
    principal_type="STAFF", principal_id="MINERU-EVALUATION-SEED", job="leader"
)


@dataclass(frozen=True)
class EvalDocumentSeed:
    """待导入的 MinerU 文档及其稳定来源标识。"""

    title: str
    markdown_path: Path
    access_label: str = "VISITOR"


class InProcessEvaluationLock:
    """提供文档向量化脚本所需的进程内互斥，不依赖 Redis。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, scene: str, key: object, *, wait_time_seconds: float = 0):
        """按场景和文档键串行执行向量化。"""
        lock = self._locks.setdefault(f"{scene}:{key}", asyncio.Lock())
        async with lock:
            yield


def _connect(settings: Settings) -> pymysql.Connection:
    """创建评测库连接。"""
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


def document_by_title(settings: Settings, title: str) -> tuple[int, str] | None:
    """按精确标题读取未删除文档，保证重复执行不重复创建。"""
    with _connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT doc_id, status FROM knowledge_document "
                "WHERE doc_title = %s AND deleted = 0 LIMIT 1",
                (title,),
            )
            row = cursor.fetchone()
    return None if row is None else (int(row[0]), str(row[1]))


async def seed_document(
    settings: Settings, seed: EvalDocumentSeed, *, apply: bool
) -> str:
    """预览或创建、切片、审核并向量化 MinerU 文档。"""
    existing = await asyncio.to_thread(document_by_title, settings, seed.title)
    if existing is not None:
        document_id, status = existing
        if apply and status.upper() in {"PENDING_REVIEW", "CHUNKED"}:
            service = DocumentReviewService(
                KnowledgeDocumentRepository(settings),
                DocumentVectorizationService(
                    KnowledgeDocumentRepository(settings),
                    KnowledgeSegmentRepository(settings),
                    ElasticsearchEmbeddingStore(
                        settings, embedder=DashScopeEmbeddingClient(settings)
                    ),
                    InProcessEvaluationLock(),  # type: ignore[arg-type]
                ),
            )
            result = await service.approve(SEED_REVIEWER, document_id, seed.access_label)  # type: ignore[arg-type]
            return f"{seed.title}：已续跑审核和向量化（{result.status}，doc_id={document_id}）"
        return f"{seed.title}：已存在（{status}，doc_id={document_id}），跳过"

    markdown = seed.markdown_path.read_text(encoding="utf-8")
    digest = sha256(markdown.encode("utf-8")).hexdigest()
    if not apply:
        return f"{seed.title}：将导入 {len(markdown)} 字符，SHA256={digest}"

    repository = KnowledgeDocumentRepository(settings)
    document_id = await repository.create_uploaded_document(
        seed.title, "mineru-evaluation-seed"
    )
    source_url = f"seed://mineru-evaluation/{document_id}"
    await repository.complete_document(document_id, source_url, source_url)
    version_id = await repository.get_current_version_id(document_id)
    chunks = MarkdownParentChildSplitter().split(
        markdown,
        document_id=document_id,
        filename=seed.markdown_path.name,
        source_url=source_url,
    )
    await KnowledgeSegmentRepository(settings).replace_for_document(
        document_id, version_id, chunks
    )
    vectorization = DocumentVectorizationService(
        repository,
        KnowledgeSegmentRepository(settings),
        ElasticsearchEmbeddingStore(settings, embedder=DashScopeEmbeddingClient(settings)),
        InProcessEvaluationLock(),  # type: ignore[arg-type]
    )
    result = await DocumentReviewService(repository, vectorization).approve(
        SEED_REVIEWER, document_id, seed.access_label  # type: ignore[arg-type]
    )
    return f"{seed.title}：已导入 {len(chunks)} 个分片并完成向量化（{result.status}，doc_id={document_id}）"


async def run(args: argparse.Namespace) -> None:
    """校验来源文件并执行预览或写入。"""
    path = args.markdown.resolve()
    if not path.is_file():
        raise SystemExit(f"MinerU 文档不存在：{path}")
    settings = Settings()
    seed = EvalDocumentSeed(args.title, path)
    print(f"来源：{path}；SHA256：{sha256(path.read_bytes()).hexdigest()}")
    print(f"模式：{'写入并向量化' if args.apply else '预览'}")
    print(await seed_document(settings, seed, apply=args.apply))


def main() -> None:
    """解析命令行参数并启动异步导入。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="导入同源 MinerU 评测文档")
    parser.add_argument(
        "--markdown",
        type=Path,
        default=root / "evals" / "fixtures" / "MinerU_markdown_r7-product-manual-for-testset.md",
    )
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--apply", action="store_true", help="确认写入隔离评测库并调用 Embedding")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
