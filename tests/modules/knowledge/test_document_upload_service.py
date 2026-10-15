from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi import UploadFile

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.api.schemas import StoredObject
from rail_rag.modules.knowledge.infrastructure.mineru_client import (
    MinerUConversionResult,
    MinerUImage,
)
from rail_rag.modules.knowledge.application.document_upload_service import (
    DocumentUploadService,
    InvalidDocumentError,
)


class FakeStorage:
    async def store_source_document(self, filename: str, content: bytes) -> StoredObject:
        assert filename in {"sample.pdf", "sample.docx"}
        return StoredObject(bucket="test", object_name="uploads/source.pdf")

    async def store_markdown(self, content: str) -> StoredObject:
        assert content == "# Parsed"
        return StoredObject(bucket="test", object_name="converted/result.md")

    async def read_bytes(self, object_name: str) -> bytes:
        assert object_name == "uploads/source.pdf"
        return b"%PDF-1.7 test"

    def stored_object(self, object_name: str) -> StoredObject:
        assert object_name == "uploads/source.pdf"
        return StoredObject(bucket="test", object_name=object_name)

    async def store_images(
        self, images: list[tuple[str, bytes, str]]
    ) -> list[StoredObject]:
        assert images == [("page.png", b"image", "image/png")]
        return [StoredObject(bucket="test", object_name="images/group/0.png")]


class FakeMinerU:
    async def convert_document(
        self, filename: str, content: bytes
    ) -> MinerUConversionResult:
        assert filename in {"sample.pdf", "sample.docx"}
        return MinerUConversionResult(
            markdown_content="# Parsed",
            images=[MinerUImage("page.png", b"image", "image/png")],
        )


@pytest.mark.asyncio
async def test_upload_pdf_stores_original_and_markdown() -> None:
    service = DocumentUploadService(Settings(), FakeStorage(), FakeMinerU())
    upload = UploadFile(filename="sample.pdf", file=BytesIO(b"%PDF-1.7 test"))

    result = await service.upload_pdf(upload)

    assert result.source.object_name == "uploads/source.pdf"
    assert result.markdown.object_name == "converted/result.md"
    assert result.images[0].object_name == "images/group/0.png"
    assert result.markdown_content == "# Parsed"


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf_content() -> None:
    service = DocumentUploadService(Settings(), FakeStorage(), FakeMinerU())
    upload = UploadFile(filename="sample.pdf", file=BytesIO(b"not a pdf"))

    with pytest.raises(InvalidDocumentError, match="not a valid document"):
        await service.upload_pdf(upload)


@pytest.mark.asyncio
async def test_upload_accepts_docx_content() -> None:
    service = DocumentUploadService(Settings(), FakeStorage(), FakeMinerU())
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
    upload = UploadFile(filename="sample.docx", file=BytesIO(content.getvalue()))

    result = await service.upload_pdf(upload)

    assert result.source.object_name == "uploads/source.pdf"
