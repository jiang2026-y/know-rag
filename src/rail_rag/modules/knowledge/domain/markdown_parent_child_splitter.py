"""Markdown 标题切分：生成用于后续检索的父子分段。

定义 MarkdownChunk、ChunkIdGenerator 与 MarkdownParentChildSplitter；切片器按 1..title_level
级标题切分章节，并为超长章节保留完整父块与带重叠的可检索子块。
"""

import re
from dataclasses import dataclass
from typing import Protocol

from rail_rag.modules.knowledge.domain.snowflake_id_generator import (
    get_snowflake_id_generator,
)


_HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HEADER_METADATA_KEYS = (
    "title",
    "subtitle",
    "subsubtitle",
    "subsubsubtitle",
    "subsubsubsubtitle",
    "subsubsubsubsubtitle",
)


@dataclass(frozen=True)
class MarkdownChunk:
    """待持久化的分段及其恢复上下文所需元数据。"""

    chunk_id: str
    text: str
    metadata: dict[str, str | int]
    skip_embedding: bool


class ChunkIdGenerator(Protocol):
    """定义分块器所需的十进制字符串 ID 生成功能。"""

    def next_id_str(self) -> str:
        """生成一个新的分块 ID。"""


class MarkdownParentChildSplitter:
    """先按 Markdown 标题切分，再为超长章节生成子分段。

    超过 chunk_size 的章节会保留一个完整父块（不向量化），并生成带重叠的可检索子块。
    子块通过 parentChunkId 关联父块，后续检索命中子块时可恢复完整上下文。
    title_level 控制参与切分的标题层级，默认 1-6 级全部参与，与既有行为一致。
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 100,
        *,
        title_level: int = 6,
        id_generator: ChunkIdGenerator | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must be at least 0 and smaller than chunk_size")
        if not 1 <= title_level <= 6:
            raise ValueError("title_level must be between 1 and 6")
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._title_level = title_level
        self._id_generator = id_generator or get_snowflake_id_generator()

    def split(
        self, markdown: str, *, document_id: int, filename: str, source_url: str
    ) -> list[MarkdownChunk]:
        """返回转换后 Markdown 文档的有序分段。"""
        base_metadata: dict[str, str | int] = {
            "docId": document_id,
            "fileName": filename,
            "url": source_url,
        }
        sections = self._sections(markdown, base_metadata)
        chunks: list[MarkdownChunk] = []
        for text, metadata in sections:
            if len(text) <= self._chunk_size:
                chunk_id = self._new_chunk_id()
                chunks.append(
                    MarkdownChunk(
                        chunk_id=chunk_id,
                        text=text,
                        metadata={**metadata, "chunkId": chunk_id},
                        skip_embedding=False,
                    )
                )
                continue

            # 父块完整保留表格、图片和标题等上下文，但不写入向量索引；
            # 子块用于高效召回，二者共同解决“检索精度”和“上下文完整性”的矛盾。
            parent_chunk_id = self._new_chunk_id()
            chunks.append(
                MarkdownChunk(
                    chunk_id=parent_chunk_id,
                    text=text,
                    metadata={
                        **metadata,
                        "chunkId": parent_chunk_id,
                        "skipEmbedding": 1,
                    },
                    skip_embedding=True,
                )
            )
            for child_text in self._split_by_size(text):
                child_chunk_id = self._new_chunk_id()
                chunks.append(
                    MarkdownChunk(
                        chunk_id=child_chunk_id,
                        text=child_text,
                        metadata={
                            **metadata,
                            "chunkId": child_chunk_id,
                            "parentChunkId": parent_chunk_id,
                        },
                        skip_embedding=False,
                    )
                )
        return chunks

    def _sections(
        self, markdown: str, base_metadata: dict[str, str | int]
    ) -> list[tuple[str, dict[str, str | int]]]:
        sections: list[tuple[str, dict[str, str | int]]] = []
        headings: list[tuple[int, str]] = []
        current_lines: list[str] = []
        current_metadata = dict(base_metadata)
        in_code_block = False
        fence = ""

        def flush() -> None:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append((text, dict(current_metadata)))
            current_lines.clear()

        for line in markdown.splitlines():
            stripped = line.strip()
            # 代码块内的井号可能是代码内容，不能误识别为 Markdown 标题。
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_code_block:
                    in_code_block = True
                    fence = marker
                elif marker == fence:
                    in_code_block = False
                    fence = ""
                current_lines.append(line)
                continue

            match = None if in_code_block else _HEADER_PATTERN.match(stripped)
            if match is None:
                current_lines.append(line)
                continue

            level = len(match.group(1))
            if level > self._title_level:
                # 更深层级的标题不参与切分，作为正文保留在当前章节内。
                current_lines.append(line)
                continue

            flush()
            title = match.group(2).strip()
            headings = [item for item in headings if item[0] < level]
            headings.append((level, title))
            current_metadata = dict(base_metadata)
            for heading_level, heading_title in headings:
                current_metadata[_HEADER_METADATA_KEYS[heading_level - 1]] = heading_title
            current_metadata["headerLevel"] = level
            current_lines.append(line)

        flush()
        return sections

    def _split_by_size(self, text: str) -> list[str]:
        pieces: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self._chunk_size, len(text))
            pieces.append(text[start:end])
            if end == len(text):
                break
            # 保留尾部重叠文本，降低关键信息恰好落在两个子块边界时的召回损失。
            start = end - self._overlap
        return pieces

    def _new_chunk_id(self) -> str:
        return self._id_generator.next_id_str()
