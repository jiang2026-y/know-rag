"""文档切片工厂：按已校验的切片参数构造对应的分片策略实现。

定义 DocumentSplitter、LengthDocumentSplitter、RegexDocumentSplitter 与
build_document_splitter，供文档处理任务按版本上的 split_param 选择 TITLE、LENGTH、
SMART、REGEX 或 SEPARATOR 策略。
"""

from __future__ import annotations

import re
from typing import Protocol

from rail_rag.modules.knowledge.domain.document_split_param import DocumentSplitParam
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    ChunkIdGenerator,
    MarkdownChunk,
    MarkdownParentChildSplitter,
)
from rail_rag.modules.knowledge.domain.snowflake_id_generator import (
    get_snowflake_id_generator,
)


class DocumentSplitter(Protocol):
    """定义文档处理任务所需的切片能力。"""

    def split(
        self, markdown: str, *, document_id: int, filename: str, source_url: str
    ) -> list[MarkdownChunk]:
        """把转换后的 Markdown 切分为有序分片。"""


class LengthDocumentSplitter:
    """按固定长度与重叠切分，不产生父子分片。"""

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 100,
        *,
        id_generator: ChunkIdGenerator | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must be at least 0 and smaller than chunk_size")
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._id_generator = id_generator or get_snowflake_id_generator()

    def split(
        self, markdown: str, *, document_id: int, filename: str, source_url: str
    ) -> list[MarkdownChunk]:
        """把整篇正文按长度切分为平铺分片。"""
        text = markdown.strip()
        if not text:
            return []
        base_metadata: dict[str, str | int] = {
            "docId": document_id,
            "fileName": filename,
            "url": source_url,
        }
        chunks: list[MarkdownChunk] = []
        for piece in _split_by_size(text, self._chunk_size, self._overlap):
            chunk_id = self._id_generator.next_id_str()
            chunks.append(
                MarkdownChunk(
                    chunk_id=chunk_id,
                    text=piece,
                    metadata={**base_metadata, "chunkId": chunk_id},
                    skip_embedding=False,
                )
            )
        return chunks


class RegexDocumentSplitter:
    """按正则或分隔符切分，超长片段再生成父子分片以保留完整上下文。"""

    def __init__(
        self,
        pattern: str,
        chunk_size: int = 1000,
        overlap: int = 100,
        *,
        id_generator: ChunkIdGenerator | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must be at least 0 and smaller than chunk_size")
        try:
            self._pattern = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid split pattern: {exc}") from exc
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._id_generator = id_generator or get_snowflake_id_generator()

    def split(
        self, markdown: str, *, document_id: int, filename: str, source_url: str
    ) -> list[MarkdownChunk]:
        """先按模式切出片段，再对超长片段做父子切分。"""
        base_metadata: dict[str, str | int] = {
            "docId": document_id,
            "fileName": filename,
            "url": source_url,
        }
        sections = [piece.strip() for piece in self._pattern.split(markdown)]
        chunks: list[MarkdownChunk] = []
        for text in sections:
            if not text:
                continue
            if len(text) <= self._chunk_size:
                chunk_id = self._id_generator.next_id_str()
                chunks.append(
                    MarkdownChunk(
                        chunk_id=chunk_id,
                        text=text,
                        metadata={**base_metadata, "chunkId": chunk_id},
                        skip_embedding=False,
                    )
                )
                continue
            parent_chunk_id = self._id_generator.next_id_str()
            chunks.append(
                MarkdownChunk(
                    chunk_id=parent_chunk_id,
                    text=text,
                    metadata={
                        **base_metadata,
                        "chunkId": parent_chunk_id,
                        "skipEmbedding": 1,
                    },
                    skip_embedding=True,
                )
            )
            for child_text in _split_by_size(text, self._chunk_size, self._overlap):
                child_chunk_id = self._id_generator.next_id_str()
                chunks.append(
                    MarkdownChunk(
                        chunk_id=child_chunk_id,
                        text=child_text,
                        metadata={
                            **base_metadata,
                            "chunkId": child_chunk_id,
                            "parentChunkId": parent_chunk_id,
                        },
                        skip_embedding=False,
                    )
                )
        return chunks


def build_document_splitter(
    param: DocumentSplitParam | None,
    *,
    id_generator: ChunkIdGenerator | None = None,
) -> DocumentSplitter:
    """按切片参数构造切片器；缺省参数等价于既有的标题父子切分。"""
    resolved = param or DocumentSplitParam()
    if resolved.split_type == "LENGTH":
        return LengthDocumentSplitter(
            resolved.chunk_size, resolved.overlap, id_generator=id_generator
        )
    if resolved.split_type == "REGEX":
        return RegexDocumentSplitter(
            resolved.regex or "",
            resolved.chunk_size,
            resolved.overlap,
            id_generator=id_generator,
        )
    if resolved.split_type == "SEPARATOR":
        return RegexDocumentSplitter(
            re.escape(resolved.separator or ""),
            resolved.chunk_size,
            resolved.overlap,
            id_generator=id_generator,
        )
    return MarkdownParentChildSplitter(
        resolved.chunk_size,
        resolved.overlap,
        title_level=resolved.title_level,
        id_generator=id_generator,
    )


def _split_by_size(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按固定长度与尾部重叠切分文本。"""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        pieces.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return pieces
