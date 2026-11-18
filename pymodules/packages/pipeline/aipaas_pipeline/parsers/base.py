"""解析器协议：新增格式只需实现 ``parse`` 并注册。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.models import ParsedDocument


@runtime_checkable
class DocumentParser(Protocol):
    name: str
    formats: tuple[str, ...]

    def parse(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        """解析字节内容；失败时抛出 ``ParseFailure``。"""
        ...
