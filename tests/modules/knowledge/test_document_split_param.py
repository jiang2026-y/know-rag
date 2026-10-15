"""切片参数与策略测试：验证五种策略的输出结构、参数校验和默认行为回归。

定义 SequentialIdGenerator 与若干测试，覆盖默认参数等价于既有标题父子切分、
TITLE 的 title_level、SMART 重叠推导、LENGTH 平铺切分、REGEX/SEPARATOR 二次切分
以及全部非法参数组合。
"""

import pytest

from rail_rag.modules.knowledge.domain.document_split_param import (
    DEFAULT_SPLIT_PARAM,
    InvalidSplitParamError,
    parse_split_param,
)
from rail_rag.modules.knowledge.domain.document_splitter_factory import (
    build_document_splitter,
)
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    MarkdownParentChildSplitter,
)


class SequentialIdGenerator:
    """为切片测试提供可预测的分片 ID。"""

    def __init__(self) -> None:
        self._next_id = 100

    def next_id_str(self) -> str:
        value = str(self._next_id)
        self._next_id += 1
        return value


MARKDOWN = "# Guide\nIntro\n## Install\nRun the installer."


def test_default_parameters_match_legacy_title_splitting() -> None:
    legacy = MarkdownParentChildSplitter(
        chunk_size=1000, overlap=100, id_generator=SequentialIdGenerator()
    ).split(
        MARKDOWN,
        document_id=7,
        filename="guide.pdf",
        source_url="minio://know-engine/uploads/guide.pdf",
    )
    current = build_document_splitter(
        parse_split_param(None), id_generator=SequentialIdGenerator()
    ).split(
        MARKDOWN,
        document_id=7,
        filename="guide.pdf",
        source_url="minio://know-engine/uploads/guide.pdf",
    )

    assert [
        (chunk.chunk_id, chunk.text, chunk.metadata, chunk.skip_embedding)
        for chunk in legacy
    ] == [
        (chunk.chunk_id, chunk.text, chunk.metadata, chunk.skip_embedding)
        for chunk in current
    ]
    assert DEFAULT_SPLIT_PARAM.split_type == "TITLE"
    assert DEFAULT_SPLIT_PARAM.title_level == 6


def test_title_level_keeps_deeper_headings_inside_section() -> None:
    splitter = build_document_splitter(
        parse_split_param({"split_type": "TITLE", "title_level": "2"}),
        id_generator=SequentialIdGenerator(),
    )

    chunks = splitter.split(
        "# A\na\n### C\nc\n## B\nb",
        document_id=1,
        filename="f.md",
        source_url="minio://bucket/f.md",
    )
    default_chunks = build_document_splitter(
        parse_split_param(None), id_generator=SequentialIdGenerator()
    ).split(
        "# A\na\n### C\nc\n## B\nb",
        document_id=1,
        filename="f.md",
        source_url="minio://bucket/f.md",
    )

    assert [chunk.text for chunk in chunks] == ["# A\na\n### C\nc", "## B\nb"]
    assert "subsubtitle" not in chunks[0].metadata
    assert chunks[1].metadata["subtitle"] == "B"
    # 默认 6 级会让三级标题也切出独立章节。
    assert [chunk.text for chunk in default_chunks] == [
        "# A\na",
        "### C\nc",
        "## B\nb",
    ]


def test_smart_split_derives_overlap_from_chunk_size() -> None:
    param = parse_split_param({"split_type": "SMART", "chunk_size": "500"})
    body = "# T\n" + "a" * 1200

    chunks = build_document_splitter(
        param, id_generator=SequentialIdGenerator()
    ).split(
        body,
        document_id=1,
        filename="f.md",
        source_url="minio://bucket/f.md",
    )
    parent, *children = chunks

    assert param.overlap == 50
    assert parent.skip_embedding is True
    assert [chunk.metadata.get("parentChunkId") for chunk in children] == [
        parent.chunk_id
    ] * len(children)
    assert len(children[0].text) == 500
    assert children[0].text[450:] == children[1].text[:50]


def test_length_split_produces_flat_overlapping_chunks() -> None:
    param = parse_split_param(
        {"split_type": "LENGTH", "chunk_size": "10", "overlap": "2"}
    )

    chunks = build_document_splitter(
        param, id_generator=SequentialIdGenerator()
    ).split(
        "abcdefghijklmnop",
        document_id=1,
        filename="f.md",
        source_url="minio://bucket/f.md",
    )

    assert [chunk.text for chunk in chunks] == ["abcdefghij", "ijklmnop"]
    assert all(chunk.skip_embedding is False for chunk in chunks)
    assert all("parentChunkId" not in chunk.metadata for chunk in chunks)


def test_regex_split_produces_parent_and_children_for_long_pieces() -> None:
    param = parse_split_param(
        {"split_type": "REGEX", "regex": "\n\n", "chunk_size": "6", "overlap": "0"}
    )

    chunks = build_document_splitter(
        param, id_generator=SequentialIdGenerator()
    ).split(
        "甲甲甲\n\n乙乙乙乙乙乙乙乙",
        document_id=1,
        filename="f.md",
        source_url="minio://bucket/f.md",
    )

    assert chunks[0].text == "甲甲甲"
    assert chunks[0].skip_embedding is False
    parent = chunks[1]
    assert parent.text == "乙乙乙乙乙乙乙乙"
    assert parent.skip_embedding is True
    assert [chunk.text for chunk in chunks[2:]] == ["乙乙乙乙乙乙", "乙乙"]
    assert all(chunk.metadata["parentChunkId"] == parent.chunk_id for chunk in chunks[2:])


def test_separator_split_uses_literal_separator() -> None:
    param = parse_split_param(
        {"split_type": "SEPARATOR", "separator": "；", "chunk_size": "50"}
    )

    chunks = build_document_splitter(
        param, id_generator=SequentialIdGenerator()
    ).split(
        "第一句；第二句",
        document_id=1,
        filename="f.md",
        source_url="minio://bucket/f.md",
    )

    assert [chunk.text for chunk in chunks] == ["第一句", "第二句"]


@pytest.mark.parametrize(
    "payload",
    [
        {"split_type": "UNKNOWN"},
        {"chunk_size": "0"},
        {"chunk_size": "9000"},
        {"chunk_size": "abc"},
        {"chunk_size": "10", "overlap": "10"},
        {"overlap": "-1"},
        {"title_level": "0"},
        {"title_level": "7"},
        {"split_type": "REGEX"},
        {"split_type": "REGEX", "regex": "(["},
        {"split_type": "SEPARATOR"},
        {"split_type": "REGEX", "regex": "a", "separator": ","},
        {"separator": "x" * 201},
    ],
)
def test_invalid_split_parameters_raise(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidSplitParamError):
        parse_split_param(payload)


def test_split_type_is_normalized_to_upper_case() -> None:
    assert parse_split_param({"split_type": " title "}).split_type == "TITLE"
    assert parse_split_param({"split_type": ""}).split_type == "TITLE"
