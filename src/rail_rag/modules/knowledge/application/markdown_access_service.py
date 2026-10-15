from pathlib import PurePosixPath

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.api.schemas import (
    MarkdownDownloadResponse,
    MarkdownPreviewResponse,
)
from rail_rag.modules.knowledge.infrastructure.minio_storage_service import MinioStorageService


class InvalidMarkdownObjectError(ValueError):
    """请求目标不属于允许访问的生成 Markdown 对象时抛出。"""


class MarkdownAccessService:
    """提供对生成的 Markdown 和图片对象的受限访问。"""

    def __init__(self, settings: Settings, storage: MinioStorageService) -> None:
        self._settings = settings
        self._storage = storage

    async def preview(self, object_name: str) -> MarkdownPreviewResponse:
        self._validate_object_name(object_name)
        content = await self._storage.read_markdown(object_name)
        return MarkdownPreviewResponse(object_name=object_name, content=content)

    async def create_download_url(self, object_name: str) -> MarkdownDownloadResponse:
        self._validate_object_name(object_name)
        return await self._presigned_download(object_name)

    async def create_image_download_url(
        self, object_name: str
    ) -> MarkdownDownloadResponse:
        self._validate_image_object_name(object_name)
        return await self._presigned_download(object_name)

    async def _presigned_download(self, object_name: str) -> MarkdownDownloadResponse:
        url = await self._storage.create_download_url(object_name)
        return MarkdownDownloadResponse(
            object_name=object_name,
            url=url,
            expires_in_seconds=self._settings.minio_presigned_url_expiry_seconds,
        )

    @staticmethod
    def _validate_object_name(object_name: str) -> None:
        path = PurePosixPath(object_name)
        if (
            not object_name
            or "\\" in object_name
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) != 2
            or path.parts[0] != "converted"
            or path.suffix.lower() != ".md"
        ):
            raise InvalidMarkdownObjectError(
                "Only generated converted/*.md objects can be accessed"
            )

    @staticmethod
    def _validate_image_object_name(object_name: str) -> None:
        path = PurePosixPath(object_name)
        allowed_suffixes = {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg"
        }
        if (
            not object_name
            or "\\" in object_name
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) != 3
            or path.parts[0] != "images"
            or not path.parts[1]
            or path.suffix.lower() not in allowed_suffixes
        ):
            raise InvalidMarkdownObjectError(
                "Only generated images/<group>/<file> objects can be accessed"
            )
