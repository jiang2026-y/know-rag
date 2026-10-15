import asyncio
from datetime import timedelta
from io import BytesIO
from pathlib import PurePosixPath
from uuid import uuid4

from minio import Minio

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.api.schemas import StoredObject


class MinioStorageService:
    """在 MinIO 中存储源文件、转换后的 Markdown 和提取出的图片。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    async def store_source_document(self, filename: str, content: bytes) -> StoredObject:
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix not in {".pdf", ".doc", ".docx"}:
            raise ValueError("Unsupported source document extension")
        return await self._store(
            object_name=f"uploads/{uuid4()}{suffix}",
            content=content,
            content_type=self._source_content_type(suffix),
        )

    async def store_markdown(self, content: str) -> StoredObject:
        return await self._store(
            object_name=f"converted/{uuid4()}.md",
            content=content.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

    async def store_images(
        self, images: list[tuple[str, bytes, str]]
    ) -> list[StoredObject]:
        group_id = uuid4()
        stored: list[StoredObject] = []
        for index, (name, content, content_type) in enumerate(images):
            safe_name = PurePosixPath(name).name
            suffix = PurePosixPath(safe_name).suffix.lower()
            if not safe_name or not suffix:
                continue
            stored.append(
                await self._store(
                    object_name=f"images/{group_id}/{index}{suffix}",
                    content=content,
                    content_type=content_type,
                )
            )
        return stored

    async def read_markdown(self, object_name: str) -> str:
        return await asyncio.to_thread(self._read_markdown, object_name)

    async def read_bytes(self, object_name: str) -> bytes:
        return await asyncio.to_thread(self._read_bytes, object_name)

    def stored_object(self, object_name: str) -> StoredObject:
        return StoredObject(
            bucket=self._settings.minio_bucket,
            object_name=object_name,
            url=self._public_url(object_name),
        )

    async def create_download_url(self, object_name: str) -> str:
        return await asyncio.to_thread(
            self._client.presigned_get_object,
            self._settings.minio_bucket,
            object_name,
            timedelta(seconds=self._settings.minio_presigned_url_expiry_seconds),
        )

    async def _store(
        self, *, object_name: str, content: bytes, content_type: str
    ) -> StoredObject:
        # MinIO SDK 为同步接口，放入线程执行，避免上传大文件阻塞事件循环。
        await asyncio.to_thread(self._ensure_bucket)
        await asyncio.to_thread(
            self._client.put_object,
            self._settings.minio_bucket,
            object_name,
            BytesIO(content),
            len(content),
            content_type=content_type,
        )
        return StoredObject(
            bucket=self._settings.minio_bucket,
            object_name=object_name,
            url=self._public_url(object_name),
        )

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._settings.minio_bucket):
            self._client.make_bucket(self._settings.minio_bucket)

    def _read_markdown(self, object_name: str) -> str:
        return self._read_bytes(object_name).decode("utf-8")

    def _read_bytes(self, object_name: str) -> bytes:
        response = self._client.get_object(self._settings.minio_bucket, object_name)
        try:
            return response.read()
        finally:
            # 释放 HTTP 连接，否则长时间运行后连接池可能耗尽。
            response.close()
            response.release_conn()

    def _public_url(self, object_name: str) -> str | None:
        if self._settings.minio_public_base_url is None:
            return None
        return f"{str(self._settings.minio_public_base_url).rstrip('/')}/{self._settings.minio_bucket}/{object_name}"

    @staticmethod
    def _source_content_type(suffix: str) -> str:
        if suffix == ".pdf":
            return "application/pdf"
        if suffix == ".doc":
            return "application/msword"
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
