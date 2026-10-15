"""文档上传与格式转换：校验文件、保存对象并将支持的格式转换为 Markdown。"""

from fastapi import UploadFile
from io import BytesIO
import csv
import io
from zipfile import BadZipFile, ZipFile

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.api.schemas import DocumentUploadResponse
from rail_rag.modules.knowledge.infrastructure.mineru_client import MinerUClient
from rail_rag.modules.knowledge.infrastructure.minio_storage_service import MinioStorageService


class InvalidDocumentError(ValueError):
    """当上传的文件不是可接受的文档时，引发（该错误/异常）"""


class DocumentUploadService:
    def __init__(
        self,
        settings: Settings,
        storage: MinioStorageService,
        mineru: MinerUClient,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._mineru = mineru

    async def upload_pdf(self, file: UploadFile) -> DocumentUploadResponse:
        filename, content = await self.read_and_validate_upload(file)
        return await self.upload_document_content(filename, content)

    async def read_and_validate_upload(self, file: UploadFile) -> tuple[str, bytes]:
        filename = file.filename or "document.pdf"
        self._validate_filename(filename)

        content = await file.read(self._settings.max_upload_size_bytes + 1)
        await file.close()
        self._validate_content(filename, content)
        return filename, content

    async def upload_document_content(
        self, filename: str, content: bytes
    ) -> DocumentUploadResponse:
        source = await self.store_source_document(filename, content)
        return await self.convert_stored_document(filename, source.object_name)

    async def store_source_document(self, filename: str, content: bytes):
        return await self._storage.store_source_document(filename, content)

    async def convert_stored_document(
        self, filename: str, source_object_name: str
    ) -> DocumentUploadResponse:
        content = await self._storage.read_bytes(source_object_name)
        if filename.lower().endswith(".csv"):
            conversion = self._convert_csv(content)
        elif filename.lower().endswith((".xls", ".xlsx")):
            conversion = await self._convert_spreadsheet(content)
        else:
            conversion = await self._mineru.convert_document(filename, content)
        markdown = await self._storage.store_markdown(conversion.markdown_content)
        images = await self._storage.store_images(
            [(image.name, image.content, image.content_type) for image in conversion.images]
        )

        return DocumentUploadResponse(
            original_filename=filename,
            source=self._storage.stored_object(source_object_name),
            markdown=markdown,
            images=images,
            markdown_content=conversion.markdown_content,
        )
    #扩展名校验
    def _validate_filename(self, filename: str) -> None:
        if not filename.lower().endswith((".pdf", ".doc", ".docx", ".csv", ".xls", ".xlsx")):
            raise InvalidDocumentError("Only PDF, DOC, DOCX, CSV, XLS, and XLSX files are supported")
    #内容校验
    def _validate_content(self, filename: str, content: bytes) -> None:
        if not content:
            raise InvalidDocumentError("The uploaded document is empty")
        if len(content) > self._settings.max_upload_size_bytes:
            raise InvalidDocumentError("The uploaded document exceeds the configured size limit")
        # 扩展名校验后再验证文件特征，可拦截最常见的“改后缀伪装”文件，
        # 同时避免为了基础校验而启动重量级文档解析器。
        suffix = filename.lower().rsplit(".", maxsplit=1)[-1]
        is_valid = (
            suffix == "pdf" and content.startswith(b"%PDF-")
        ) or (
            suffix == "doc" and content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        ) or (suffix == "docx" and self._is_docx(content)) \
            or (suffix == "csv" and b"\x00" not in content)
        if suffix in {"xls", "xlsx"}:
            is_valid = len(content) >= 8
        if not is_valid:
            raise InvalidDocumentError("The uploaded file is not a valid document")

    @staticmethod
    def _convert_csv(content: bytes):
        """将 UTF-8 CSV 转换为 Markdown 表格。"""
        from rail_rag.modules.knowledge.infrastructure.mineru_client import DocumentConversionResult
        try:
            rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise InvalidDocumentError("CSV could not be parsed") from exc
        if not rows or len(rows) > 10000 or max((len(row) for row in rows), default=0) > 100:
            raise InvalidDocumentError("CSV exceeds row or column limits")
        width = max(len(row) for row in rows)
        normalized = [row + [""] * (width - len(row)) for row in rows]
        markdown = "| " + " | ".join(normalized[0]) + " |\n| " + " | ".join(["---"] * width) + " |"
        if len(normalized) > 1:
            markdown += "\n" + "\n".join("| " + " | ".join(row) + " |" for row in normalized[1:])
        return DocumentConversionResult(markdown_content=markdown, images=[])

    @staticmethod
    async def _convert_spreadsheet(content: bytes):
        """使用可选 pandas 依赖将 XLS/XLSX 表格转换为 Markdown。"""
        from rail_rag.modules.knowledge.infrastructure.mineru_client import DocumentConversionResult
        try:
            import pandas as pd
            sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, nrows=10000)
        except Exception as exc:
            raise InvalidDocumentError("Spreadsheet could not be parsed") from exc
        if len(sheets) > 50:
            raise InvalidDocumentError("Spreadsheet has too many worksheets")
        sections = []
        for name, frame in sheets.items():
            if frame.shape[1] > 100:
                raise InvalidDocumentError("Spreadsheet has too many columns")
            sections.append(f"## {name}\n\n{frame.fillna('').to_markdown(index=False)}")
        return DocumentConversionResult(markdown_content="\n\n".join(sections), images=[])

    @staticmethod
    def _is_docx(content: bytes) -> bool:
        try:
            with ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
                return "[Content_Types].xml" in names and "word/document.xml" in names
        except BadZipFile:
            return False
