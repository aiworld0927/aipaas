from __future__ import annotations

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.errors import FailureCodes, ParseFailure
from aipaas_pipeline.options import ParseOptions
from aipaas_pipeline.parsers.pdf_parser import PdfParser
from aipaas_pipeline.registry import ParserRegistry
from aipaas_pipeline.samples import pdf_garbled, pdf_gbt_35273, pdf_jrt_0197, pdf_scanned


def test_text_pdf_extracts_standard_clauses_tree_and_tables() -> None:
    parser = PdfParser()
    document = parser.parse(pdf_gbt_35273(), ParseContext(doc_id="doc1", format="pdf"))
    assert document.success
    assert document.standard_no == "GB/T 35273-2020"
    assert "个人信息安全规范" in document.title
    clauses = {clause.clause_no: clause for clause in document.clauses}
    assert {"4", "4.1", "4.1.1", "4.1.2", "5", "5.1"} <= set(clauses)
    assert clauses["4.1.1"].page_no == 1
    assert clauses["5.1"].page_no == 2
    assert "最小必要" in clauses["4.1.2"].text
    assert clauses["4.1.1"].chapter_path.endswith("4.1.1 总则")
    assert {"4", "5"} <= {node.clause_no for node in document.chapter_tree}
    assert document.tables, "应识别出网格表格"
    assert any("最小必要" in cell for row in document.tables[0].rows for cell in row)
    assert document.pages == 2


def test_annex_headings_are_parsed() -> None:
    parser = PdfParser()
    document = parser.parse(pdf_jrt_0197(), ParseContext(doc_id="doc2", format="pdf"))
    assert document.standard_no == "JR/T 0197-2020"
    numbers = {clause.clause_no for clause in document.clauses}
    assert {"1", "4", "4.1", "4.1.1", "附录A", "A.1"} <= numbers


def test_scanned_pdf_fails_with_explicit_code_and_warning() -> None:
    parser = PdfParser()
    try:
        parser.parse(pdf_scanned(), ParseContext(doc_id="scan", format="pdf"))
    except ParseFailure as failure:
        assert failure.code == FailureCodes.PDF_SCANNED
        assert failure.retryable is False
        assert failure.page_no == 1
        assert any(warning.code == "PARSE.PAGE_NO_TEXT" for warning in failure.warnings)
    else:  # pragma: no cover
        raise AssertionError("扫描件必须失败")


def test_garbled_pdf_fails_with_garbled_code() -> None:
    parser = PdfParser()
    try:
        parser.parse(pdf_garbled(), ParseContext(doc_id="garbled", format="pdf"))
    except ParseFailure as failure:
        assert failure.code == FailureCodes.PDF_GARBLED
        assert failure.retryable is True
    else:  # pragma: no cover
        raise AssertionError("乱码 PDF 必须失败")


class _FlakyPdfParser(PdfParser):
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def parse(self, data: bytes, ctx: ParseContext):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ParseFailure("PARSE.FLAKY", "临时失败", retryable=True, page_no=1)
        return super().parse(data, ctx)


def test_retry_at_most_twice_then_success(service) -> None:
    registry = ParserRegistry()
    flaky = _FlakyPdfParser(fail_times=2)
    registry.register(flaky)
    service.registry = registry
    document = service.parse(pdf_gbt_35273(), doc_id="flaky-ok", filename="a.pdf",
                             options=ParseOptions(retry_backoff_seconds=0))
    assert document.success
    assert document.retry_count == 2
    assert flaky.calls == 3


def test_retry_never_exceeds_two(service) -> None:
    registry = ParserRegistry()
    flaky = _FlakyPdfParser(fail_times=99)
    registry.register(flaky)
    service.registry = registry
    document = service.parse(pdf_gbt_35273(), doc_id="flaky-fail", filename="a.pdf",
                             options=ParseOptions(retry_backoff_seconds=0))
    assert not document.success
    assert document.failure_code == "PARSE.FLAKY"
    assert document.retry_count == 2
    assert flaky.calls == 3
