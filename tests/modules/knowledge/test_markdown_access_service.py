import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.application.markdown_access_service import (
    InvalidMarkdownObjectError,
    MarkdownAccessService,
)


class FakeStorage:
    async def read_markdown(self, object_name: str) -> str:
        assert object_name == "converted/result.md"
        return "# Parsed"

    async def create_download_url(self, object_name: str) -> str:
        assert object_name in {"converted/result.md", "images/group/page.png"}
        return "http://minio.test/signed-url"


@pytest.mark.asyncio
async def test_preview_returns_generated_markdown() -> None:
    service = MarkdownAccessService(Settings(), FakeStorage())

    result = await service.preview("converted/result.md")

    assert result.content == "# Parsed"


@pytest.mark.asyncio
async def test_download_returns_short_lived_url() -> None:
    settings = Settings(minio_presigned_url_expiry_seconds=600)
    service = MarkdownAccessService(settings, FakeStorage())

    result = await service.create_download_url("converted/result.md")

    assert result.url == "http://minio.test/signed-url"
    assert result.expires_in_seconds == 600


@pytest.mark.asyncio
async def test_image_download_returns_short_lived_url() -> None:
    service = MarkdownAccessService(Settings(), FakeStorage())

    result = await service.create_image_download_url("images/group/page.png")

    assert result.object_name == "images/group/page.png"


@pytest.mark.asyncio
@pytest.mark.parametrize("object_name", ["uploads/input.pdf", "../converted/x.md", "converted/nested/x.md"])
async def test_rejects_objects_outside_generated_markdown(object_name: str) -> None:
    service = MarkdownAccessService(Settings(), FakeStorage())

    with pytest.raises(InvalidMarkdownObjectError):
        await service.preview(object_name)
