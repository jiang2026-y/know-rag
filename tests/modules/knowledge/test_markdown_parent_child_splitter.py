from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    MarkdownParentChildSplitter,
)


class SequentialIdGenerator:
    """为分块器测试提供可预测的雪花 ID 形式字符串。"""

    def __init__(self) -> None:
        self._next_id = 100

    def next_id_str(self) -> str:
        value = str(self._next_id)
        self._next_id += 1
        return value


def test_title_metadata_is_preserved_for_nested_sections() -> None:
    splitter = MarkdownParentChildSplitter(chunk_size=100, overlap=10)

    chunks = splitter.split(
        "# Guide\nIntro\n## Install\nRun the installer.",
        document_id=7,
        filename="guide.pdf",
        source_url="minio://know-engine/uploads/guide.pdf",
    )

    assert [chunk.text for chunk in chunks] == [
        "# Guide\nIntro",
        "## Install\nRun the installer.",
    ]
    assert chunks[1].metadata["title"] == "Guide"
    assert chunks[1].metadata["subtitle"] == "Install"
    assert chunks[1].metadata["headerLevel"] == 2
    assert chunks[1].metadata["docId"] == 7
    assert chunks[1].skip_embedding is False


def test_oversized_title_section_has_parent_and_overlapping_children() -> None:
    splitter = MarkdownParentChildSplitter(
        chunk_size=10, overlap=2, id_generator=SequentialIdGenerator()
    )

    chunks = splitter.split(
        "# Large\nabcdefghijklmno",
        document_id=7,
        filename="guide.pdf",
        source_url="minio://know-engine/uploads/guide.pdf",
    )

    parent, first_child, second_child, third_child = chunks
    assert parent.text == "# Large\nabcdefghijklmno"
    assert parent.chunk_id == "100"
    assert parent.skip_embedding is True
    assert parent.metadata["skipEmbedding"] == 1
    assert first_child.skip_embedding is False
    assert first_child.chunk_id == "101"
    assert first_child.metadata["parentChunkId"] == parent.chunk_id
    assert first_child.text == "# Large\nab"
    assert second_child.text == "abcdefghij"
    assert third_child.text == "ijklmno"


def test_code_block_heading_is_not_used_as_a_split_boundary() -> None:
    splitter = MarkdownParentChildSplitter(chunk_size=100, overlap=10)

    chunks = splitter.split(
        "# Title\n```markdown\n## not a title\n```\nText",
        document_id=1,
        filename="code.pdf",
        source_url="minio://know-engine/uploads/code.pdf",
    )

    assert len(chunks) == 1
    assert "## not a title" in chunks[0].text
