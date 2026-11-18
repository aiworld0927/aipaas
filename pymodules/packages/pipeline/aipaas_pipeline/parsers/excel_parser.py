"""#20 Excel 解析（openpyxl 处理 xlsx，xlrd 处理 xls）。

逐 sheet 保留单元格文本与行列结构；公式按配置取计算结果或原文；空 sheet 只告警不失败。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from io import BytesIO
from typing import Any

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.errors import FailureCodes, ParseFailure
from aipaas_pipeline.models import Clause, ParsedDocument, SectionNode, TableBlock, WarningItem
from aipaas_pipeline.normalize import (
    extract_standard_no,
    infer_title,
    make_section_id,
    match_heading,
    normalize_inline,
    normalize_text,
)

_MAX_ROWS = 5000
_MAX_COLUMNS = 200


class ExcelParser:
    """Excel 工作簿解析器，支持多 sheet。"""

    name = "xlsx"
    formats = ("xlsx", "xls")

    def parse(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        if not data:
            raise ParseFailure(FailureCodes.EMPTY_DOCUMENT, "Excel 内容为空", retryable=False)
        fmt = (ctx.format or "").lower()
        if fmt == "xls":
            return self._parse_xls(data, ctx)
        return self._parse_xlsx(data, ctx)

    # ------------------------------------------------------------------ xlsx

    def _parse_xlsx(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        try:
            from openpyxl import load_workbook

            source_book = load_workbook(BytesIO(data), data_only=False, read_only=False)
            values_book = (
                load_workbook(BytesIO(data), data_only=True, read_only=False)
                if ctx.options.formula_mode == "result"
                else None
            )
        except Exception as exc:
            code = FailureCodes.XLSX_CORRUPTED
            raise ParseFailure(code, f"Excel(xlsx) 无法打开：{str(exc)[:300]}", retryable=True) from exc

        warnings: list[WarningItem] = []
        sheet_payloads: list[dict[str, Any]] = []
        formula_count = 0
        pages = len(source_book.sheetnames)

        try:
            for index, name in enumerate(source_book.sheetnames):
                page_no = index + 1
                source_sheet = source_book[name]
                values_sheet = values_book[name] if values_book is not None else source_sheet
                rows, sheet_formulas = self._xlsx_rows(
                    source_sheet, values_sheet, ctx, page_no, warnings
                )
                formula_count += sheet_formulas
                sheet_payloads.append({"name": name, "pageNo": page_no, "rows": rows})
                if not rows:
                    warnings.append(
                        WarningItem(
                            "PARSE.EMPTY_SHEET",
                            f"工作表「{name}」为空，已跳过但不影响任务成功",
                            page_no=page_no,
                            element=f"sheet[{name}]",
                        )
                    )
        finally:
            source_book.close()
            if values_book is not None:
                values_book.close()

        return self._assemble(sheet_payloads, warnings, ctx, pages=pages,
                              extras={"formulaCount": formula_count, "formulaMode": ctx.options.formula_mode,
                                      "sheetNames": [sheet["name"] for sheet in sheet_payloads]})

    def _xlsx_rows(
        self,
        source_sheet: Any,
        values_sheet: Any,
        ctx: ParseContext,
        page_no: int,
        warnings: list[WarningItem],
    ) -> tuple[list[list[str]], int]:
        max_rows = min(source_sheet.max_row or 0, _MAX_ROWS)
        max_cols = min(source_sheet.max_column or 0, _MAX_COLUMNS)
        if (source_sheet.max_row or 0) > _MAX_ROWS or (source_sheet.max_column or 0) > _MAX_COLUMNS:
            warnings.append(
                WarningItem(
                    "PARSE.SHEET_TRUNCATED",
                    f"工作表「{source_sheet.title}」超过 {_MAX_ROWS} 行或 {_MAX_COLUMNS} 列，已截断",
                    page_no=page_no,
                    element=f"sheet[{source_sheet.title}]",
                )
            )
        matrix: list[list[str]] = []
        formulas = 0
        for row_index in range(1, max_rows + 1):
            row: list[str] = []
            for col_index in range(1, max_cols + 1):
                source_cell = source_sheet.cell(row=row_index, column=col_index)
                if ctx.options.formula_mode == "source":
                    raw = source_cell.value
                else:
                    raw = values_sheet.cell(row=row_index, column=col_index).value
                    formula = source_cell.value if source_cell.data_type == "f" else None
                    if isinstance(formula, str) and formula.startswith("="):
                        formulas += 1
                        if raw is None:
                            self._warn_formula(warnings, source_sheet.title, source_cell.coordinate, page_no)
                            raw = formula
                row.append(_cell_text(raw))
            matrix.append(row)
        self._fill_merged(source_sheet, matrix)
        return _trim_matrix(matrix), formulas

    @staticmethod
    def _warn_formula(
        warnings: list[WarningItem], sheet_name: str, coordinate: str, page_no: int
    ) -> None:
        code = "PARSE.FORMULA_NO_CACHED_VALUE"
        if sum(1 for warning in warnings if warning.code == code) >= 20:
            return
        warnings.append(
            WarningItem(
                code,
                f"单元格 {coordinate} 无缓存计算结果，已回退公式原文",
                page_no=page_no,
                element=f"sheet[{sheet_name}]!{coordinate}",
            )
        )

    @staticmethod
    def _fill_merged(sheet: Any, matrix: list[list[str]]) -> None:
        try:
            merged = list(sheet.merged_cells.ranges)
        except Exception:
            return
        for cell_range in merged:
            min_row, min_col = cell_range.min_row, cell_range.min_col
            if min_row - 1 >= len(matrix) or min_col - 1 >= len(matrix[0] if matrix else []):
                continue
            anchor = matrix[min_row - 1][min_col - 1]
            if not anchor:
                continue
            for row in range(min_row - 1, min(cell_range.max_row, len(matrix))):
                for col in range(min_col - 1, min(cell_range.max_col, len(matrix[row]))):
                    if not matrix[row][col]:
                        matrix[row][col] = anchor

    # ------------------------------------------------------------------ xls

    def _parse_xls(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        try:
            import xlrd

            book = xlrd.open_workbook(file_contents=data, on_demand=True)
        except Exception as exc:
            raise ParseFailure(
                FailureCodes.XLS_CORRUPTED, f"Excel(xls) 无法打开：{str(exc)[:300]}", retryable=True
            ) from exc

        warnings: list[WarningItem] = []
        sheets: list[dict[str, Any]] = []
        pages = book.nsheets
        if ctx.options.formula_mode == "source":
            warnings.append(
                WarningItem(
                    "PARSE.XLS_FORMULA_SOURCE_UNSUPPORTED",
                    "旧版 xls 仅能读取公式缓存值，formula_mode=source 已降级为 result",
                    element="workbook",
                )
            )
        try:
            for index in range(book.nsheets):
                sheet = book.sheet_by_index(index)
                page_no = index + 1
                rows: list[list[str]] = []
                max_rows = min(sheet.nrows, _MAX_ROWS)
                max_cols = min(sheet.ncols, _MAX_COLUMNS)
                for row_index in range(max_rows):
                    row = [
                        _cell_text(_xls_cell_value(book, sheet.cell(row_index, col_index)))
                        for col_index in range(max_cols)
                    ]
                    rows.append(row)
                rows = _trim_matrix(rows)
                sheets.append({"name": sheet.name, "pageNo": page_no, "rows": rows})
                if not rows:
                    warnings.append(
                        WarningItem(
                            "PARSE.EMPTY_SHEET",
                            f"工作表「{sheet.name}」为空，已跳过但不影响任务成功",
                            page_no=page_no,
                            element=f"sheet[{sheet.name}]",
                        )
                    )
        finally:
            book.release_resources()

        return self._assemble(sheets, warnings, ctx, pages=pages,
                              extras={"formulaMode": "result", "sheetNames": [s["name"] for s in sheets]})

    # ------------------------------------------------------------------ 统一组装

    def _assemble(
        self,
        sheets: list[dict[str, Any]],
        warnings: list[WarningItem],
        ctx: ParseContext,
        *,
        pages: int,
        extras: dict[str, Any],
    ) -> ParsedDocument:
        roots: list[SectionNode] = []
        clauses: list[Clause] = []
        tables: list[TableBlock] = []
        raw_blocks: list[str] = []
        section_index = 0

        for sheet in sheets:
            rows: list[list[str]] = sheet["rows"]
            page_no: int = sheet["pageNo"]
            sheet_name: str = sheet["name"]
            if not rows:
                continue
            raw_blocks.append(f"# Sheet: {sheet_name}\n" + "\n".join("\t".join(row) for row in rows))
            section_index += 1
            sheet_section = SectionNode(
                section_id=make_section_id(section_index),
                title=sheet_name,
                level=1,
                page_no=page_no,
            )
            roots.append(sheet_section)
            header = rows[0] if rows else []
            tables.append(
                TableBlock(
                    table_id=f"tbl_xlsx_s{page_no}",
                    page_no=page_no,
                    rows=rows,
                    caption=sheet_name,
                    header_rows=1 if header else 0,
                )
            )
            sheet_clauses: list[Clause] = []
            for row in rows[1:]:
                joined = " | ".join(cell for cell in row if cell)
                if not joined:
                    continue
                candidate = next((cell for cell in row[:3] if cell), "")
                heading = match_heading(candidate, max_level=ctx.options.max_heading_level)
                if heading is None and re.fullmatch(r"\d{1,2}(?:\.\d{1,3}){0,5}", candidate.strip()):
                    title_cell = next((cell for cell in row[1:3] if cell), "")
                    if title_cell:
                        heading = match_heading(
                            f"{candidate.strip()} {title_cell}", max_level=ctx.options.max_heading_level
                        )
                if heading is None:
                    continue
                section_index += 1
                clause_section_id = make_section_id(section_index)
                sheet_clauses.append(
                    Clause(
                        clause_no=heading.clause_no,
                        title=heading.title,
                        text=joined,
                        chapter_path=f"{sheet_name} / {heading.clause_no} {heading.title}",
                        level=min(ctx.options.max_heading_level, heading.level + 1),
                        page_no=page_no,
                        section_id=clause_section_id,
                    )
                )
                sheet_section.children.append(
                    SectionNode(
                        section_id=clause_section_id,
                        title=f"{heading.clause_no} {heading.title}",
                        level=2,
                        clause_no=heading.clause_no,
                        page_no=page_no,
                    )
                )
            if sheet_clauses:
                clauses.extend(sheet_clauses)

        raw_text = normalize_text("\n\n".join(raw_blocks))
        if not raw_text:
            raise ParseFailure(
                FailureCodes.EMPTY_DOCUMENT,
                "Excel 所有工作表均为空",
                retryable=False,
                warnings=warnings,
            )

        standard_no = ctx.standard_no_hint or extract_standard_no(raw_text[:8000])
        title = _excel_title(sheets, standard_no)

        return ParsedDocument(
            doc_id=ctx.doc_id,
            tenant_id=ctx.tenant_id,
            parser=self.name if (ctx.format or "").lower() != "xls" else "xls",
            success=True,
            title=title,
            standard_no=standard_no,
            chapter_tree=roots,
            clauses=clauses,
            tables=tables,
            raw_text=raw_text,
            warnings=warnings,
            pages=pages,
            extras=extras,
        )


def _excel_title(sheets: list[dict[str, Any]], standard_no: str | None) -> str:
    """Excel 标题：优先首个 sheet 的首个标题行（通常含标准编号），否则取 sheet 名。"""
    for sheet in sheets:
        rows: list[list[str]] = sheet["rows"]
        if not rows:
            continue
        first_cell = normalize_inline(rows[0][0] if rows[0] else "")
        if len(first_cell) >= 4 and match_heading(first_cell) is None:
            candidate = infer_title([first_cell], standard_no)
            if candidate != "未命名标准文档" and not candidate.endswith("。"):
                return candidate
        return str(sheet["name"])
    return "未命名标准文档"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, "g")
    return normalize_inline(str(value))


def _trim_matrix(rows: list[list[str]]) -> list[list[str]]:
    trimmed = [row for row in rows if any(cell for cell in row)]
    if not trimmed:
        return []
    width = max(len(row) for row in trimmed)
    while width > 0 and all((row[width - 1] if width - 1 < len(row) else "") == "" for row in trimmed):
        width -= 1
    return [[(row[col] if col < len(row) else "") for col in range(width)] for row in trimmed]


def _xls_cell_value(book: Any, cell: Any) -> Any:
    try:
        import xlrd

        if cell.ctype == xlrd.XL_CELL_DATE:
            return xlrd.xldate_as_datetime(cell.value, book.datemode)
        if cell.ctype == xlrd.XL_CELL_BOOLEAN:
            return bool(cell.value)
    except Exception:
        pass
    return cell.value
