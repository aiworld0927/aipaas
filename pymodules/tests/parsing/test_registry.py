from __future__ import annotations

import pytest

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.errors import FailureCodes, ParseFailure
from aipaas_pipeline.models import ParsedDocument
from aipaas_pipeline.registry import ParserRegistry, get_default_registry


class _CustomParser:
    name = "custom"
    formats = ("foo",)

    def parse(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        return ParsedDocument(
            doc_id=ctx.doc_id,
            parser=self.name,
            success=True,
            title="custom",
            chapter_tree=[],
            clauses=[],
            tables=[],
            raw_text=data.decode(),
        )


def test_builtin_formats_registered() -> None:
    registry = get_default_registry()
    assert {"pdf", "docx", "doc", "xlsx", "xls", "md", "markdown"} <= set(registry.formats())


def test_unknown_format_fails_explicitly() -> None:
    registry = get_default_registry()
    with pytest.raises(ParseFailure) as error:
        registry.get("exe")
    assert error.value.code == FailureCodes.UNSUPPORTED_FORMAT
    assert error.value.retryable is False


def test_custom_parser_is_pluggable() -> None:
    registry = ParserRegistry()
    registry.register(_CustomParser())
    result = registry.parse("foo", b"hello", ParseContext(doc_id="d1", format="foo"))
    assert result.success
    assert result.parser == "custom"
    assert "foo" in registry.formats()

    duplicate = _CustomParser()
    with pytest.raises(ValueError):
        registry.register(duplicate)
    registry.register(duplicate, replace=True)
