"""解析器注册表：按格式注册、可插拔，未知格式显式失败。"""

from __future__ import annotations

import threading
from typing import Iterable

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.errors import FailureCodes, ParseFailure
from aipaas_pipeline.models import ParsedDocument
from aipaas_pipeline.parsers.base import DocumentParser


class ParserRegistry:
    """线程安全的格式→解析器映射。"""

    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}
        self._lock = threading.RLock()

    def register(self, parser: DocumentParser, *, replace: bool = False) -> None:
        with self._lock:
            for fmt in parser.formats:
                key = normalize_format(fmt)
                if key in self._parsers and not replace:
                    raise ValueError(f"格式 {key} 已注册解析器 {self._parsers[key].name}")
                self._parsers[key] = parser

    def unregister(self, fmt: str) -> None:
        with self._lock:
            self._parsers.pop(normalize_format(fmt), None)

    def get(self, fmt: str) -> DocumentParser:
        key = normalize_format(fmt)
        parser = self._parsers.get(key)
        if parser is None:
            raise ParseFailure(
                FailureCodes.UNSUPPORTED_FORMAT,
                f"暂不支持 {fmt or '未知'} 格式，可用格式：{', '.join(self.formats())}",
                retryable=False,
            )
        return parser

    def formats(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._parsers))

    def parse(self, fmt: str, data: bytes, ctx: ParseContext) -> ParsedDocument:
        return self.get(fmt).parse(data, ctx)

    def __contains__(self, fmt: object) -> bool:
        return isinstance(fmt, str) and normalize_format(fmt) in self._parsers


def normalize_format(fmt: str) -> str:
    return (fmt or "").strip().lower().lstrip(".")


_DEFAULT_REGISTRY: ParserRegistry | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_registry() -> ParserRegistry:
    """返回内置解析器注册表（进程内单例，首次调用时注册）。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is not None:
        return _DEFAULT_REGISTRY
    with _DEFAULT_LOCK:
        if _DEFAULT_REGISTRY is None:
            registry = ParserRegistry()
            for parser in _builtin_parsers():
                registry.register(parser)
            _DEFAULT_REGISTRY = registry
    return _DEFAULT_REGISTRY


def _builtin_parsers() -> Iterable[DocumentParser]:
    # 延迟导入，避免模块循环依赖与可选依赖在 import 阶段失败。
    from aipaas_pipeline.parsers.docx_parser import DocxParser
    from aipaas_pipeline.parsers.excel_parser import ExcelParser
    from aipaas_pipeline.parsers.markdown_parser import MarkdownParser
    from aipaas_pipeline.parsers.pdf_parser import PdfParser

    return (PdfParser(), DocxParser(), ExcelParser(), MarkdownParser())
