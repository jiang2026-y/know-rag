"""文档向量化服务：按页扫描 MySQL 分片并同步到 Elasticsearch。

定义 DocumentVectorizationService；它校验文档状态、每页读取 100 个待处理子分片、
调用 Embedding/ES 存储服务并回写向量状态，最终标记文档完成向量化。
"""

from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    ElasticsearchEmbeddingStore,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    KnowledgeSegmentRepository,
)
from rail_rag.modules.knowledge.infrastructure.redis_distributed_lock import RedisDistributedLock


class DocumentVectorizationService:
    """按受限分页为一个文档尚未向量化的子分段生成并保存向量。"""

    _PAGE_SIZE = 100

    def __init__(
        self,
        document_repository: KnowledgeDocumentRepository,
        segment_repository: KnowledgeSegmentRepository,
        embedding_store: ElasticsearchEmbeddingStore,
        lock: RedisDistributedLock,
    ) -> None:
        self._document_repository = document_repository
        self._segment_repository = segment_repository
        self._embedding_store = embedding_store
        self._lock = lock

    async def vectorize_document(self, document_id: int, version_id: int | None = None) -> bool:
        async with self._lock.acquire(
            "document-split", document_id, wait_time_seconds=0
        ):
            document = await self._document_repository.get(document_id)
            if document is None:
                return False
            if document.status.upper() == "VECTOR_STORED":
                return True

            active_version_id = version_id or document.version_id
            if active_version_id is None:
                raise ValueError(f"Knowledge document has no active version: {document_id}")

            # 重试或重新向量化前先清空该文档旧 ES 向量，避免旧版本与新版本混合召回。
            await self._embedding_store.reset_document(document_id)
            while True:
                segments = await self._segment_repository.list_pending_for_embedding(
                    document_id, active_version_id, limit=self._PAGE_SIZE
                )
                if not segments:
                    break
                # ES 批量写入成功后才回写 MySQL 状态；失败时保留待处理状态以便重试。
                stored = await self._embedding_store.store_segments(segments)
                await self._segment_repository.mark_vectors_stored(stored)

            # 只有全部待处理子分段都完成后，文档才可标记为已建立向量索引。
            await self._document_repository.update_status(document_id, "VECTOR_STORED")
            return True
