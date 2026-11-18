"""解析失败体系：错误码必须可定位到页码或元素，且不得被静默吞掉。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aipaas_pipeline.models import WarningItem


class ParseFailure(Exception):
    """可预期解析失败。

    Attributes:
        code: 契约风格错误码（``PARSE.*``）。
        retryable: 是否值得自动重试（扫描件等确定性问题为 False）。
        page_no: 失败页码（1 起），未知为 None。
        element: 失败元素定位，例如 ``page[3].table[0]``。
        warnings: 失败时仍需回传给调用方的告警（如逐页无文本）。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = True,
        page_no: int | None = None,
        element: str | None = None,
        warnings: list["WarningItem"] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.page_no = page_no
        self.element = element
        self.warnings = warnings or []


class FailureCodes:
    """解析失败码常量。"""

    UNSUPPORTED_FORMAT = "PARSE.UNSUPPORTED_FORMAT"
    EMPTY_DOCUMENT = "PARSE.EMPTY_DOCUMENT"
    PDF_CORRUPTED = "PARSE.PDF_CORRUPTED"
    PDF_ENCRYPTED = "PARSE.PDF_ENCRYPTED"
    PDF_SCANNED = "PARSE.PDF_SCANNED"
    PDF_GARBLED = "PARSE.PDF_GARBLED"
    DOC_LEGACY_UNSUPPORTED = "PARSE.DOC_LEGACY_UNSUPPORTED"
    DOCX_CORRUPTED = "PARSE.DOCX_CORRUPTED"
    XLS_CORRUPTED = "PARSE.XLS_CORRUPTED"
    XLSX_CORRUPTED = "PARSE.XLSX_CORRUPTED"
    MARKDOWN_CORRUPTED = "PARSE.MARKDOWN_CORRUPTED"
    INTERNAL = "PARSE.INTERNAL"
