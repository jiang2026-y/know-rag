"""MinerU R7 测评文档集成测试：验证处理后的 Markdown 可被当前父子切片器解析并生成可检索子块。"""

from pathlib import Path

from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    MarkdownParentChildSplitter,
)


FIXTURE = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "MinerU_markdown_r7-product-manual-for-testset.md"


def test_mineru_r7_fixture_is_readable_and_chunked() -> None:
    """验证 MinerU 测评文档存在、包含标题，并能生成父子分片。"""
    markdown = FIXTURE.read_text(encoding="utf-8")
    assert len(markdown) > 40_000
    assert "#" in markdown

    chunks = MarkdownParentChildSplitter(chunk_size=1200, overlap=120).split(
        markdown,
        document_id=9001,
        filename=FIXTURE.name,
        source_url="fixture://mineru-r7",
    )

    assert len(chunks) > 50
    assert any(chunk.skip_embedding for chunk in chunks)
    assert any(chunk.metadata.get("parentChunkId") for chunk in chunks)
    assert all(chunk.metadata["docId"] == 9001 for chunk in chunks)
    assert all(chunk.metadata["fileName"] == FIXTURE.name for chunk in chunks)
