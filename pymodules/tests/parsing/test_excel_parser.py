from __future__ import annotations

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.options import ParseOptions
from aipaas_pipeline.parsers.excel_parser import ExcelParser
from aipaas_pipeline.samples import xls_legacy, xlsx_gbt_clauses, xlsx_jrt_multi


def test_xlsx_clauses_merged_cells_and_structure() -> None:
    document = ExcelParser().parse(xlsx_gbt_clauses(), ParseContext(doc_id="x1", format="xlsx"))
    assert document.success
    assert document.pages == 1
    assert {clause.clause_no for clause in document.clauses} >= {"4.1.1", "4.1.2", "4.1.3", "5.1"}
    assert document.tables[0].rows[0][0] == document.tables[0].rows[0][2]
    assert document.tables[0].header_rows == 1


def test_xlsx_multiple_sheets_empty_sheet_and_formula_modes() -> None:
    parser = ExcelParser()
    result_mode = parser.parse(
        xlsx_jrt_multi(), ParseContext(doc_id="x2", format="xlsx",
                                       options=ParseOptions(formula_mode="result"))
    )
    assert result_mode.success, "空 sheet 不应导致失败"
    assert result_mode.pages == 3
    assert any(warning.code == "PARSE.EMPTY_SHEET" for warning in result_mode.warnings)
    assert any(warning.code == "PARSE.FORMULA_NO_CACHED_VALUE" for warning in result_mode.warnings)
    requirements = [table for table in result_mode.tables if table.caption == "要求"][0]
    assert requirements.rows[-1][1] == "=COUNTA(A2:A3)"

    source_mode = parser.parse(
        xlsx_jrt_multi(), ParseContext(doc_id="x3", format="xlsx",
                                       options=ParseOptions(formula_mode="source"))
    )
    assert source_mode.success
    requirements = [table for table in source_mode.tables if table.caption == "要求"][0]
    assert requirements.rows[-1][1] == "=COUNTA(A2:A3)"


def test_xls_legacy_workbook() -> None:
    document = ExcelParser().parse(xls_legacy(), ParseContext(doc_id="x4", format="xls"))
    assert document.success
    assert document.parser == "xls"
    assert {clause.clause_no for clause in document.clauses} >= {"4.1.1", "4.1.2", "5.1"}
    assert document.tables[0].rows[0] == ["条款号", "要求", "说明"]
