from io import BytesIO
import json
from zipfile import ZipFile

import httpx
import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.infrastructure.mineru_client import MinerUClient, MinerUError


def _zip_with_markdown(markdown: str, *, with_image: bool = False) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("result/full.md", markdown)
        if with_image:
            archive.writestr("result/images/page.png", b"image bytes")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_converts_pdf_through_precision_extract_api() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v4/file-urls/batch":
            assert request.headers["authorization"] == "Bearer test-token"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://uploads.example/document.pdf"],
                    },
                },
            )
        if request.url.host == "uploads.example":
            assert request.method == "PUT"
            assert request.content == b"%PDF-1.7 test"
            return httpx.Response(200)
        if request.url.path == "/api/v4/extract-results/batch/batch-1":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": json.loads(requests[0].content)["files"][0]["data_id"],
                                "state": "done",
                                "full_zip_url": "https://cdn.example/result.zip",
                            }
                        ]
                    },
                },
            )
        if request.url.host == "cdn.example":
            return httpx.Response(200, content=_zip_with_markdown("# Parsed"))
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    settings = Settings(
        mineru_api_base_url="https://mineru.net",
        mineru_api_token="test-token",
        mineru_task_poll_interval_seconds=0.001,
    )
    client = MinerUClient(settings, transport=httpx.MockTransport(handler))

    result = await client.convert_pdf_to_markdown("sample.pdf", b"%PDF-1.7 test")

    assert result == "# Parsed"
    assert [request.method for request in requests] == ["POST", "PUT", "GET", "GET"]


def test_result_zip_extracts_markdown_and_images() -> None:
    result = MinerUClient._conversion_from_zip(
        _zip_with_markdown("# Parsed", with_image=True)
    )

    assert result.markdown_content == "# Parsed"
    assert [(image.name, image.content_type, image.content) for image in result.images] == [
        ("page.png", "image/png", b"image bytes")
    ]


@pytest.mark.asyncio
async def test_requires_precision_extract_token() -> None:
    client = MinerUClient(Settings(mineru_api_token=None))

    with pytest.raises(MinerUError, match="MINERU_API_TOKEN"):
        await client.convert_pdf_to_markdown("sample.pdf", b"%PDF-1.7 test")


@pytest.mark.asyncio
async def test_includes_upstream_http_error_details() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "A0202", "msg": "Token error"})

    client = MinerUClient(
        Settings(mineru_api_token="test-token"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(MinerUError, match=r"HTTP 401.*Token error"):
        await client.convert_pdf_to_markdown("sample.pdf", b"%PDF-1.7 test")


@pytest.mark.asyncio
async def test_includes_network_error_target_url() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection dropped", request=request)

    client = MinerUClient(
        Settings(mineru_api_token="test-token"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(MinerUError, match=r"network request failed.*file-urls/batch"):
        await client.convert_pdf_to_markdown("sample.pdf", b"%PDF-1.7 test")


@pytest.mark.asyncio
async def test_retries_transient_result_zip_download_failure() -> None:
    zip_download_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal zip_download_attempts
        if request.url.path == "/api/v4/file-urls/batch":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://uploads.example/document.pdf"],
                    },
                },
            )
        if request.url.host == "uploads.example":
            return httpx.Response(200)
        if request.url.path == "/api/v4/extract-results/batch/batch-1":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "state": "done",
                                "full_zip_url": "https://cdn.example/result.zip",
                            }
                        ]
                    },
                },
            )
        if request.url.host == "cdn.example":
            zip_download_attempts += 1
            if zip_download_attempts == 1:
                raise httpx.ConnectError("connection dropped", request=request)
            return httpx.Response(200, content=_zip_with_markdown("# Retried"))
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = MinerUClient(
        Settings(
            mineru_api_token="test-token",
            mineru_result_download_retry_attempts=2,
            mineru_result_download_retry_delay_seconds=0.001,
        ),
        transport=httpx.MockTransport(handler),
    )

    assert await client.convert_pdf_to_markdown("sample.pdf", b"%PDF-1.7 test") == "# Retried"
    assert zip_download_attempts == 2


@pytest.mark.asyncio
async def test_reports_exhausted_result_zip_download_retries() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/file-urls/batch":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://uploads.example/document.pdf"],
                    },
                },
            )
        if request.url.host == "uploads.example":
            return httpx.Response(200)
        if request.url.path == "/api/v4/extract-results/batch/batch-1":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "state": "done",
                                "full_zip_url": "https://cdn.example/result.zip",
                            }
                        ]
                    },
                },
            )
        raise httpx.ConnectError("connection dropped", request=request)

    client = MinerUClient(
        Settings(
            mineru_api_token="test-token",
            mineru_result_download_retry_attempts=2,
            mineru_result_download_retry_delay_seconds=0.001,
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(MinerUError, match=r"ZIP download failed after 2 attempts"):
        await client.convert_pdf_to_markdown("sample.pdf", b"%PDF-1.7 test")
