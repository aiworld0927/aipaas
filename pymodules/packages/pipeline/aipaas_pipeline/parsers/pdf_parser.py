"""#18 PDF 文本解析（PyMuPDF）。

产出章节树、条款原文、表格与页码；扫描件/乱码给出明确失败码与告警。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pymupdf

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.errors import FailureCodes, ParseFailure
from aipaas_pipeline.models import Clause, ParsedDocument, SectionNode, TableBlock, WarningItem
from aipaas_pipeline.normalize import (
    HeadingMatch,
    chapter_path,
    extract_standard_no,
    garbled_ratio,
    infer_title,
    is_table_caption,
    make_section_id,
    match_heading,
    normalize_inline,
    normalize_text,
    table_caption_text,
)


@dataclass(slots=True)
class _PdfLine:
    text: str
    page_no: int
    size: float
    bold: bool
    y: float


class PdfParser:
    """文本型 PDF 原生解析器。"""

    name = "pdf-native"
    formats = ("pdf",)

    def parse(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        if not data:
            raise ParseFailure(FailureCodes.EMPTY_DOCUMENT, "PDF 内容为空", retryable=False)
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:  # PyMuPDF 的异常类型随版本变化，统一归类
            raise ParseFailure(
                FailureCodes.PDF_CORRUPTED, f"PDF 无法打开：{str(exc)[:300]}", retryable=True
            ) from exc

        try:
            return self._parse_document(document, ctx)
        finally:
            document.close()

    def _parse_document(self, document: "pymupdf.Document", ctx: ParseContext) -> ParsedDocument:
        if document.is_encrypted or document.needs_pass:
            raise ParseFailure(FailureCodes.PDF_ENCRYPTED, "PDF 已加密，无法解析文本", retryable=False)
        pages = document.page_count
        if pages <= 0:
            raise ParseFailure(FailureCodes.EMPTY_DOCUMENT, "PDF 不包含任何页面", retryable=False)

        page_texts: list[str] = []
        lines: list[_PdfLine] = []
        tables: list[TableBlock] = []
        warnings: list[WarningItem] = []
        empty_pages: list[int] = []

        for page_index in range(pages):
            page_no = page_index + 1
            page = document.load_page(page_index)
            text = page.get_text("text") or ""
            page_texts.append(text)
            if not text.strip():
                empty_pages.append(page_no)

            page_lines = self._page_lines(page, page_no)
            lines.extend(page_lines)
            self._extract_tables(page, page_no, page_lines, tables, warnings)

        raw_text = normalize_text("\n".join(page_texts))
        total_chars = len(raw_text.replace("\n", "").strip())
        bad_chars, bad_ratio = garbled_ratio(raw_text)

        if empty_pages:
            warnings.append(
                WarningItem(
                    "PARSE.PAGE_NO_TEXT",
                    f"{len(empty_pages)} 页未提取到文本，可能为扫描页",
                    page_no=empty_pages[0],
                    element=f"pages={empty_pages[:20]}",
                )
            )
        if bad_chars:
            warnings.append(
                WarningItem(
                    "PARSE.GARBLED_TEXT",
                    f"检测到 {bad_chars} 个乱码字符，占比 {bad_ratio:.1%}",
                    element="rawText",
                )
            )

        # 扫描件：几乎没有可抽取文本，但页面存在。
        if total_chars == 0 or (total_chars < max(30, pages * 10) and not tables):
            raise ParseFailure(
                FailureCodes.PDF_SCANNED,
                f"PDF 未提取到有效文本（{pages} 页，有效字符 {total_chars}），判定为扫描件",
                retryable=False,
                page_no=empty_pages[0] if empty_pages else 1,
                element="pages",
                warnings=warnings,
            )
        if bad_ratio > 0.15:
            raise ParseFailure(
                FailureCodes.PDF_GARBLED,
                f"PDF 文本乱码占比 {bad_ratio:.1%}，无法可靠识别标准编号与条款",
                retryable=True,
                element="rawText",
                warnings=warnings,
            )

        body_size = self._body_size(lines)
        chapter_tree, clauses = self._build_structure(lines, body_size, ctx)
        if not clauses:
            warnings.append(
                WarningItem("PARSE.NO_CLAUSE", "未识别到带编号条款，已保留章节树与原文", element="clauses")
            )

        standard_no = ctx.standard_no_hint or extract_standard_no("\n".join(page_texts[:2]))
        metadata_title = ""
        try:
            metadata_title = normalize_inline(str(document.metadata.get("title") or ""))
        except Exception:
            metadata_title = ""
        title = metadata_title or infer_title(raw_text.split("\n"), standard_no)

        return ParsedDocument(
            doc_id=ctx.doc_id,
            tenant_id=ctx.tenant_id,
            parser=self.name,
            success=True,
            title=title,
            standard_no=standard_no,
            chapter_tree=chapter_tree,
            clauses=clauses,
            tables=tables,
            raw_text=raw_text,
            warnings=warnings,
            pages=pages,
            extras={
                "pageCharCounts": [len(text.strip()) for text in page_texts],
                "emptyPages": empty_pages,
                "tableCount": len(tables),
            },
        )

    # ------------------------------------------------------------------ 行提取

    @staticmethod
    def _page_lines(page: "pymupdf.Page", page_no: int) -> list[_PdfLine]:
        result: list[_PdfLine] = []
        payload = page.get_text("dict")
        for block in payload.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span.get("text", "") for span in spans).strip()
                if not text:
                    continue
                size = max((float(span.get("size", 0.0)) for span in spans), default=0.0)
                bold = any(
                    "bold" in str(span.get("font", "")).lower() or int(span.get("flags", 0)) & 16
                    for span in spans
                )
                bbox = line.get("bbox") or (0, 0, 0, 0)
                result.append(_PdfLine(text=text, page_no=page_no, size=size, bold=bold, y=float(bbox[1])))
        return result

    @staticmethod
    def _body_size(lines: list[_PdfLine]) -> float:
        sizes: list[float] = []
        for line in lines:
            sizes.extend([line.size] * max(1, len(line.text)))
        return statistics.median(sizes) if sizes else 10.0

    # ------------------------------------------------------------------ 表格

    def _extract_tables(
        self,
        page: "pymupdf.Page",
        page_no: int,
        page_lines: list[_PdfLine],
        tables: list[TableBlock],
        warnings: list[WarningItem],
    ) -> None:
        try:
            finder = page.find_tables()
            found = list(getattr(finder, "tables", []) or [])
        except Exception as exc:
            warnings.append(
                WarningItem(
                    "PARSE.TABLE_DETECTION_FAILED",
                    f"第 {page_no} 页表格识别失败：{str(exc)[:200]}",
                    page_no=page_no,
                    element=f"page[{page_no}].tables",
                )
            )
            return
        for index, table in enumerate(found):
            try:
                extracted = table.extract() or []
            except Exception as exc:
                warnings.append(
                    WarningItem(
                        "PARSE.TABLE_EXTRACT_FAILED",
                        f"第 {page_no} 页第 {index + 1} 个表格提取失败：{str(exc)[:200]}",
                        page_no=page_no,
                        element=f"page[{page_no}].table[{index}]",
                    )
                )
                continue
            rows = [
                [normalize_inline(str(cell).replace("\n", " ")) if cell is not None else "" for cell in row]
                for row in extracted
            ]
            rows = [row for row in rows if any(cell for cell in row)]
            if not rows:
                continue
            caption = self._table_caption(page_lines, table)
            tables.append(
                TableBlock(
                    table_id=f"tbl_p{page_no}_{index + 1}",
                    page_no=page_no,
                    rows=rows,
                    caption=caption,
                    header_rows=1,
                )
            )

    @staticmethod
    def _table_caption(page_lines: list[_PdfLine], table: object) -> str | None:
        bbox = getattr(table, "bbox", None) or (0, 0, 0, 0)
        top = float(bbox[1])
        candidates = [
            line for line in page_lines if line.y <= top + 2 and top - line.y <= 60 and is_table_caption(line.text)
        ]
        if not candidates:
            return None
        return table_caption_text(max(candidates, key=lambda item: item.y).text)

    # ------------------------------------------------------------------ 结构

    def _build_structure(
        self, lines: list[_PdfLine], body_size: float, ctx: ParseContext
    ) -> tuple[list[SectionNode], list[Clause]]:
        roots: list[SectionNode] = []
        node_stack: list[SectionNode] = []
        path_stack: list[tuple[str, str]] = []
        clauses: list[Clause] = []
        current: Clause | None = None
        buffer: list[str] = []
        section_index = 0

        def flush_clause() -> None:
            nonlocal current, buffer
            if current is not None:
                current.text = "\n".join(part for part in buffer if part).strip()
                clauses.append(current)
                current = None
                buffer = []

        for line in lines:
            text = line.text.strip()
            if not text:
                continue
            heading = match_heading(text, max_level=ctx.options.max_heading_level)
            if heading is None and self._is_font_heading(line, body_size):
                heading = self._font_heading(line, body_size, ctx.options.max_heading_level)
            is_clause = heading is not None and heading.kind in {"clause", "chapter", "annex"}

            if heading is not None:
                flush_clause()
                section_index += 1
                section_id = make_section_id(section_index)
                while node_stack and node_stack[-1].level >= heading.level:
                    node_stack.pop()
                while path_stack and len(path_stack) >= heading.level:
                    path_stack.pop()
                node = SectionNode(
                    section_id=section_id,
                    title=heading.title,
                    level=heading.level,
                    clause_no=heading.clause_no or None,
                    page_no=line.page_no,
                )
                if node_stack:
                    node_stack[-1].children.append(node)
                else:
                    roots.append(node)
                node_stack.append(node)
                path_stack.append((heading.clause_no, heading.title))
                if is_clause:
                    current = Clause(
                        clause_no=heading.clause_no,
                        title=heading.title,
                        text="",
                        chapter_path=chapter_path(path_stack),
                        level=heading.level,
                        page_no=line.page_no,
                        section_id=section_id,
                    )
                continue

            if current is not None:
                if is_table_caption(text):
                    continue
                buffer.append(text)

        flush_clause()
        return roots, clauses

    @staticmethod
    def _is_font_heading(line: _PdfLine, body_size: float) -> bool:
        if body_size <= 0 or len(line.text) > 80 or len(line.text) < 2:
            return False
        if is_table_caption(line.text):
            return False
        if line.size < body_size * 1.18:
            return False
        return line.bold or line.size >= body_size * 1.28

    @staticmethod
    def _font_heading(line: _PdfLine, body_size: float, max_level: int) -> HeadingMatch:
        ratio = line.size / body_size if body_size > 0 else 1.0
        if ratio >= 1.6:
            level = 1
        elif ratio >= 1.45:
            level = 2
        elif ratio >= 1.3:
            level = 3
        else:
            level = 4
        return HeadingMatch("", line.text.strip(), min(level, max_level), "font")
