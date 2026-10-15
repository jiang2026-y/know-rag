"""检索上下文扩展服务：回溯父块、按需补相邻子块并执行 MySQL 二次授权。

定义 ExpandedKnowledgeContext 和 ContextExpansionService；服务以请求内字典为 L1、Redis 为 L2、
MySQL 为 L3 恢复分片正文，短父块完整补回，长父块使用命中子块及自适应相邻窗口补全，
并且只把通过 MySQL 二次授权校验的正文送入模型上下文。
"""

from dataclasses import dataclass, field
from typing import Protocol

from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    RetrievedEmbeddingSegment,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    AuthorizedKnowledgeSegment,
    KnowledgeSegmentRepository,
)


class SegmentTextCache(Protocol):
    """定义父块回溯所需的二级正文缓存能力。"""

    async def get_text(self, chunk_id: str) -> str | None:
        """返回缓存正文；未命中时返回 None。"""

    async def set_text(
        self, chunk_id: str, text: str, *, document_id: int | None = None
    ) -> None:
        """写入分片正文缓存。"""


@dataclass(frozen=True)
class ExpandedKnowledgeContext:
    """二次授权且经过父子块补全后可送入模型的知识上下文。"""

    chunk_id: str
    text: str
    score: float
    document_id: int
    retrieval_methods: tuple[str, ...] = ()
    raw_scores: dict[str, float] = field(default_factory=dict)
    rrf_score: float | None = None
    document_title: str = ""
    section: str = ""


class ContextExpansionService:
    """按当前数据库授权恢复子块所属章节的完整或局部上下文。"""

    _SHORT_PARENT_MAX_CHARACTERS = 4000
    _BASE_NEIGHBORS_PER_SIDE = 1
    _MAX_NEIGHBORS_PER_SIDE = 2
    _SENTENCE_ENDINGS = frozenset("。！？!?；;：:\n")

    def __init__(
        self,
        segment_repository: KnowledgeSegmentRepository,
        text_cache: SegmentTextCache | None = None,
    ) -> None:
        self._segment_repository = segment_repository
        self._text_cache = text_cache

    async def expand(
        self,
        hits: list[RetrievedEmbeddingSegment],
        accessible_by: list[str],
    ) -> list[ExpandedKnowledgeContext]:
        """按照 ES 原始排序，返回已完成二次授权和局部补全的上下文。"""
        if not hits or not accessible_by:
            return []
        # L1：同一次请求内的正文缓存，避免同一分片被反复读取。
        request_texts: dict[str, str | None] = {}
        references = await self._authorized_refs(
            [hit.chunk_id for hit in hits], accessible_by
        )
        references_by_id = {reference.chunk_id: reference for reference in references}
        result: list[ExpandedKnowledgeContext] = []
        headers_cache: dict[tuple[int, int], list[AuthorizedKnowledgeSegment]] = {}
        parent_cache: dict[str, AuthorizedKnowledgeSegment | None] = {}

        for hit in hits:
            reference = references_by_id.get(hit.chunk_id)
            if reference is None:
                # ES 可能尚未删掉旧向量；找不到或无权的命中必须直接丢弃。
                continue
            parent_chunk_id = reference.metadata.get("parentChunkId")
            if not isinstance(parent_chunk_id, str) or not parent_chunk_id:
                text = await self._text_for(
                    reference.chunk_id, reference.document_id, request_texts
                )
                if text is None:
                    continue
                result.append(self._context(reference, hit, text))
                continue

            parent = parent_cache.get(parent_chunk_id)
            if parent_chunk_id not in parent_cache:
                parents = await self._authorized_refs([parent_chunk_id], accessible_by)
                parent = next(
                    (
                        item
                        for item in parents
                        if item.document_id == reference.document_id
                        and item.document_version == reference.document_version
                    ),
                    None,
                )
                parent_cache[parent_chunk_id] = parent
            parent_text = (
                await self._text_for(parent.chunk_id, parent.document_id, request_texts)
                if parent is not None
                else None
            )
            if parent_text is not None and len(parent_text) <= self._SHORT_PARENT_MAX_CHARACTERS:
                # 短父块可以整段补回，命中子块直接升级为完整章节上下文。
                result.append(self._context(reference, hit, parent_text))
                continue

            cache_key = (reference.document_id, reference.document_version)
            headers = headers_cache.get(cache_key)
            if headers is None:
                headers = await self._segment_repository.list_authorized_headers(
                    reference.document_id, reference.document_version, accessible_by
                )
                headers_cache[cache_key] = headers
            sibling_ids = self._sibling_window_ids(reference, parent_chunk_id, headers)
            sibling_texts: list[str] = []
            for sibling_id in sibling_ids:
                sibling_text = await self._text_for(
                    sibling_id, reference.document_id, request_texts
                )
                if sibling_text:
                    sibling_texts.append(sibling_text)
            window_text = "\n\n".join(sibling_texts).strip()
            hit_text = await self._text_for(
                reference.chunk_id, reference.document_id, request_texts
            )
            result.append(
                self._context(reference, hit, window_text or hit_text or reference.text)
            )
        return result

    async def _authorized_refs(
        self, chunk_ids: list[str], accessible_by: list[str]
    ) -> list[AuthorizedKnowledgeSegment]:
        """只取二次授权结论和分片元数据，正文留给缓存层按需补齐。"""
        return await self._segment_repository.get_authorized_refs_by_chunk_ids(
            chunk_ids, accessible_by
        )

    async def _text_for(
        self, chunk_id: str, document_id: int, cache: dict[str, str | None]
    ) -> str | None:
        """按 L1 请求内缓存、L2 Redis、L3 MySQL 的顺序恢复分片正文。"""
        if chunk_id in cache:
            return cache[chunk_id]
        if self._text_cache is not None:
            cached_text = await self._text_cache.get_text(chunk_id)
            if cached_text is not None:
                cache[chunk_id] = cached_text
                return cached_text
        texts = await self._segment_repository.get_text_by_chunk_ids([chunk_id])
        text = texts.get(chunk_id)
        cache[chunk_id] = text
        if text is not None and self._text_cache is not None:
            await self._text_cache.set_text(chunk_id, text, document_id=document_id)
        return text

    def _sibling_window_ids(
        self,
        hit: AuthorizedKnowledgeSegment,
        parent_chunk_id: str,
        headers: list[AuthorizedKnowledgeSegment],
    ) -> list[str]:
        """按窗口和文本边界决定长父块需要补回的相邻子块。"""
        siblings = [
            item
            for item in headers
            if item.metadata.get("parentChunkId") == parent_chunk_id
        ]
        siblings.sort(key=lambda item: item.chunk_order)
        try:
            index = next(item_index for item_index, item in enumerate(siblings) if item.chunk_id == hit.chunk_id)
        except StopIteration:
            return [hit.chunk_id]

        left_count = min(self._BASE_NEIGHBORS_PER_SIDE, index)
        right_count = min(self._BASE_NEIGHBORS_PER_SIDE, len(siblings) - index - 1)
        if (
            left_count < self._MAX_NEIGHBORS_PER_SIDE
            and index - left_count > 0
            and not self._ends_at_boundary(siblings[index - left_count - 1])
        ):
            left_count += 1
        if (
            right_count < self._MAX_NEIGHBORS_PER_SIDE
            and index + right_count < len(siblings) - 1
            and not self._ends_at_boundary(siblings[index + right_count])
        ):
            right_count += 1
        return [item.chunk_id for item in siblings[index - left_count : index + right_count + 1]]

    def _ends_at_boundary(self, header: AuthorizedKnowledgeSegment) -> bool:
        """按分片正文尾部的段落或句子结束符判断是否仍需向右补全。"""
        return header.text.rstrip().endswith(tuple(self._SENTENCE_ENDINGS))

    @staticmethod
    def _context(
        reference: AuthorizedKnowledgeSegment,
        hit: RetrievedEmbeddingSegment,
        text: str,
    ) -> ExpandedKnowledgeContext:
        """把已授权的命中、正文和检索元数据组合为模型可用的上下文。"""
        return ExpandedKnowledgeContext(
            chunk_id=reference.chunk_id,
            text=text,
            score=hit.score,
            document_id=reference.document_id,
            retrieval_methods=hit.retrieval_methods,
            raw_scores=hit.raw_scores,
            rrf_score=hit.rrf_score,
            document_title=reference.document_title,
            section=ContextExpansionService._section_from_metadata(reference.metadata),
        )

    @staticmethod
    def _section_from_metadata(metadata: dict[str, object]) -> str:
        """按 Markdown 标题层级提取用户可读章节路径，不暴露内部分片 ID。"""
        keys = (
            "title",
            "subtitle",
            "subsubtitle",
            "subsubsubtitle",
            "subsubsubsubtitle",
            "subsubsubsubsubtitle",
        )
        return " / ".join(
            str(metadata[key]).strip()
            for key in keys
            if isinstance(metadata.get(key), str) and str(metadata[key]).strip()
        )
