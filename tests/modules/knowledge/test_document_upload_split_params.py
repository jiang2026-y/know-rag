"""文档上传切片参数接口测试：验证非法参数返回 422、合法参数透传给任务服务。

定义 FakeUploadService、FakeTaskService 与两项测试，确保参数校验发生在读取文件之前，
且校验后的切片参数以规范化形式进入 DocumentTaskService.submit。
"""

import pytest
from fastapi import HTTPException

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.api.document_controller import upload_document
from rail_rag.modules.knowledge.api.schemas import (
    DocumentTaskState,
    DocumentUploadTaskAccepted,
)
from rail_rag.modules.knowledge.domain.document_split_param import DocumentSplitParam


class FakeUploadService:
    """记录文件读取次数，验证参数非法时不会读取上传内容。"""

    def __init__(self) -> None:
        self.read_calls = 0

    async def read_and_validate_upload(self, file):
        self.read_calls += 1
        return "guide.pdf", b"%PDF-1.7 test"


class FakeTaskService:
    """记录提交到任务服务的文件名、上传人和切片参数。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def submit(self, filename, content, upload_user, *, split_param=None):
        self.calls.append((filename, content, upload_user, split_param))
        return DocumentUploadTaskAccepted(
            task_id="task-1",
            document_id=1,
            state=DocumentTaskState.QUEUED,
            document_status="UPLOADED",
            status_url="/api/documents/tasks/task-1",
        )


def staff_principal() -> SessionPrincipal:
    """创建一名普通员工的可信主体。"""
    return SessionPrincipal(principal_type="STAFF", principal_id="EMP-1", job="staff")


@pytest.mark.asyncio
async def test_invalid_split_parameters_return_422_before_reading_file() -> None:
    upload_service = FakeUploadService()
    task_service = FakeTaskService()

    with pytest.raises(HTTPException) as error:
        await upload_document(
            file=None,  # type: ignore[arg-type]
            upload_service=upload_service,  # type: ignore[arg-type]
            task_service=task_service,  # type: ignore[arg-type]
            principal=staff_principal(),
            split_type="REGEX",
        )

    assert error.value.status_code == 422
    assert upload_service.read_calls == 0
    assert task_service.calls == []


@pytest.mark.asyncio
async def test_valid_split_parameters_are_normalized_and_passed_through() -> None:
    upload_service = FakeUploadService()
    task_service = FakeTaskService()

    accepted = await upload_document(
        file=None,  # type: ignore[arg-type]
        upload_service=upload_service,  # type: ignore[arg-type]
        task_service=task_service,  # type: ignore[arg-type]
        principal=staff_principal(),
        split_type="separator",
        separator="\n\n",
        chunk_size=800,
    )

    assert accepted.task_id == "task-1"
    assert upload_service.read_calls == 1
    filename, _content, upload_user, split_param = task_service.calls[0]
    assert filename == "guide.pdf"
    assert upload_user == "EMP-1"
    assert split_param == DocumentSplitParam(
        split_type="SEPARATOR",
        chunk_size=800,
        overlap=100,
        title_level=6,
        separator="\n\n",
    )


@pytest.mark.asyncio
async def test_customer_upload_is_rejected() -> None:
    with pytest.raises(HTTPException) as error:
        await upload_document(
            file=None,  # type: ignore[arg-type]
            upload_service=FakeUploadService(),  # type: ignore[arg-type]
            task_service=FakeTaskService(),  # type: ignore[arg-type]
            principal=SessionPrincipal(
                principal_type="CUSTOMER", principal_id="customer-1"
            ),
        )

    assert error.value.status_code == 403
