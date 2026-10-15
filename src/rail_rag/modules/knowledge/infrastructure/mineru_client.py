import asyncio
from dataclasses import dataclass
from io import BytesIO
from mimetypes import guess_type
from pathlib import PurePosixPath
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import httpx

from rail_rag.config.settings import Settings


class MinerUError(RuntimeError):
    """远程 MinerU 服务无法完成文档转换时抛出。"""


@dataclass(frozen=True)
class MinerUImage:
    """MinerU 结果压缩包中返回的一张图片。"""

    name: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class MinerUConversionResult:
    markdown_content: str
    images: list[MinerUImage]


class MinerUClient:
    """MinerU Precision Extract API 客户端，负责鉴权上传、轮询和结果解析。"""

    _MAX_RESULT_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
    _MAX_RESULT_IMAGES = 1_000

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._api_base_url = str(settings.mineru_api_base_url).rstrip("/")
        self._api_token = settings.mineru_api_token
        self._model_version = settings.mineru_model_version
        self._poll_interval_seconds = settings.mineru_task_poll_interval_seconds
        self._task_timeout_seconds = settings.mineru_task_timeout_seconds
        self._result_download_retry_attempts = (
            settings.mineru_result_download_retry_attempts
        )
        self._result_download_retry_delay_seconds = (
            settings.mineru_result_download_retry_delay_seconds
        )
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.mineru_connect_timeout_seconds,
            read=settings.mineru_response_timeout_seconds,
            write=settings.mineru_response_timeout_seconds,
            pool=settings.mineru_connect_timeout_seconds,
        )

    async def convert_document(
        self, filename: str, content: bytes
    ) -> MinerUConversionResult:
        """上传文档、轮询异步任务，并读取转换得到的 Markdown 和图片。

        云端接口先返回带签名的上传地址；源文件上传后，必须根据任务状态轮询，
        最后下载包含 Markdown 和图片的结果压缩包。
        """
        headers = self._authorization_headers()
        data_id = str(uuid4())

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, trust_env=False
            ) as client:
                upload_request = await client.post(
                    f"{self._api_base_url}/api/v4/file-urls/batch",
                    headers=headers,
                    json={
                        "files": [{"name": filename, "data_id": data_id}],
                        "model_version": self._model_version,
                    },
                )
                upload_data = self._response_data(upload_request)
                batch_id = self._required_string(upload_data, "batch_id")
                upload_url = self._first_string(upload_data, "file_urls")

                # 上传地址仅用于本次文件传输；获取后立即上传，避免其过期。
                upload_response = await client.put(upload_url, content=content)
                upload_response.raise_for_status()

                # batch_id 标识一批任务，data_id 用于从批次结果中定位当前文件。
                zip_url = await self._wait_for_result(client, headers, batch_id, data_id)
                zip_response = await self._download_result_zip(client, zip_url)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            detail = response.text.strip()
            if len(detail) > 1_000:
                detail = f"{detail[:1_000]}..."
            message = detail or response.reason_phrase or "no response body"
            raise MinerUError(
                f"MinerU request failed with HTTP {response.status_code}: {message}"
            ) from exc
        except httpx.RequestError as exc:
            request_url = self._safe_request_url(exc.request)
            message = str(exc).strip() or exc.__class__.__name__
            raise MinerUError(
                f"MinerU network request failed for {request_url}: {message}"
            ) from exc

        return self._conversion_from_zip(zip_response.content)

    async def convert_pdf_to_markdown(self, filename: str, content: bytes) -> str:
        """兼容仅需获取 Markdown 内容的既有调用方。"""
        return (await self.convert_document(filename, content)).markdown_content

    def _authorization_headers(self) -> dict[str, str]:
        if self._api_token is None:
            raise MinerUError(
                "MINERU_API_TOKEN is required for MinerU Precision Extract API"
            )
        token = self._api_token.get_secret_value().strip()
        if not token:
            raise MinerUError(
                "MINERU_API_TOKEN is required for MinerU Precision Extract API"
            )
        return {"Authorization": f"Bearer {token}"}

    async def _wait_for_result(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        batch_id: str,
        data_id: str,
    ) -> str:
        deadline = monotonic() + self._task_timeout_seconds
        url = f"{self._api_base_url}/api/v4/extract-results/batch/{batch_id}"

        while monotonic() < deadline:
            response = await client.get(url, headers=headers)
            result_data = self._response_data(response)
            result = self._matching_result(result_data, data_id)
            state = result.get("state")

            if state == "done":
                return self._required_string(result, "full_zip_url")
            if state == "failed":
                message = result.get("err_msg") or "MinerU extraction failed"
                raise MinerUError(str(message))

            await asyncio.sleep(self._poll_interval_seconds)

        raise MinerUError(
            f"MinerU task did not finish within {self._task_timeout_seconds} seconds"
        )

    async def _download_result_zip(
        self, client: httpx.AsyncClient, zip_url: str
    ) -> httpx.Response:
        for attempt in range(1, self._result_download_retry_attempts + 1):
            try:
                # 每次 CDN 重试都新建客户端，以重新建立连接并解析 DNS，
                # 避免复用已失效连接导致后续重试持续失败。
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                    trust_env=False,
                ) as download_client:
                    response = await download_client.get(zip_url)
                    response.raise_for_status()
                    return response
            except httpx.RequestError as exc:
                if attempt == self._result_download_retry_attempts:
                    request_url = self._safe_request_url(exc.request)
                    message = str(exc).strip() or exc.__class__.__name__
                    raise MinerUError(
                        "MinerU result ZIP download failed "
                        f"after {attempt} attempts for {request_url}: {message}"
                    ) from exc
                delay = min(
                    self._result_download_retry_delay_seconds * (2 ** (attempt - 1)),
                    30,
                )
                await asyncio.sleep(delay)

        raise AssertionError("unreachable")

    @staticmethod
    def _response_data(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUError("MinerU returned a non-JSON response") from exc

        if not isinstance(payload, dict):
            raise MinerUError("MinerU returned an invalid response")
        if payload.get("code") != 0:
            message = payload.get("msg") or "MinerU API request failed"
            raise MinerUError(str(message))

        data = payload.get("data")
        if not isinstance(data, dict):
            raise MinerUError("MinerU response did not contain data")
        return data

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise MinerUError(f"MinerU response did not contain {key}")
        return value

    @classmethod
    def _first_string(cls, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            raise MinerUError(f"MinerU response did not contain {key}")
        first = value[0]
        if not isinstance(first, str) or not first:
            raise MinerUError(f"MinerU response did not contain {key}")
        return first

    @staticmethod
    def _matching_result(payload: dict[str, Any], data_id: str) -> dict[str, Any]:
        results = payload.get("extract_result")
        if not isinstance(results, list):
            raise MinerUError("MinerU response did not contain extraction results")

        for result in results:
            if isinstance(result, dict) and result.get("data_id") == data_id:
                return result
        if len(results) == 1 and isinstance(results[0], dict):
            return results[0]
        raise MinerUError("MinerU response did not contain the uploaded document")

    @staticmethod
    def _safe_request_url(request: httpx.Request | None) -> str:
        if request is None:
            return "unknown URL"
        parts = urlsplit(str(request.url))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _conversion_from_zip(content: bytes) -> MinerUConversionResult:
        try:
            with ZipFile(BytesIO(content)) as archive:
                total_uncompressed_size = sum(info.file_size for info in archive.infolist())
                # 限制解压后的总大小，防止异常压缩包消耗过多内存。
                if total_uncompressed_size > MinerUClient._MAX_RESULT_UNCOMPRESSED_BYTES:
                    raise MinerUError("MinerU result ZIP exceeds the 100 MiB extraction limit")
                names = archive.namelist()
                markdown_name = next(
                    (name for name in names if name.endswith("/full.md") or name == "full.md"),
                    None,
                )
                if markdown_name is None:
                    markdown_name = next(
                        (name for name in names if name.lower().endswith(".md")), None
                    )
                if markdown_name is None:
                    raise MinerUError("MinerU ZIP did not contain a Markdown file")
                markdown_content = archive.read(markdown_name).decode("utf-8")
                images: list[MinerUImage] = []
                for info in archive.infolist():
                    path = PurePosixPath(info.filename)
                    suffix = path.suffix.lower()
                    # 拒绝绝对路径和包含上级目录的条目，规避 ZIP 路径穿越风险。
                    if (
                        info.is_dir()
                        or path.is_absolute()
                        or ".." in path.parts
                        or suffix
                        not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg"}
                    ):
                        continue
                    if len(images) >= MinerUClient._MAX_RESULT_IMAGES:
                        raise MinerUError(
                            "MinerU result ZIP exceeds the 1000-image extraction limit"
                        )
                    content_type = guess_type(path.name)[0] or "application/octet-stream"
                    images.append(
                        MinerUImage(
                            name=path.name,
                            content=archive.read(info),
                            content_type=content_type,
                        )
                    )
                return MinerUConversionResult(
                    markdown_content=markdown_content, images=images
                )
        except BadZipFile as exc:
            raise MinerUError("MinerU result was not a valid ZIP archive") from exc
