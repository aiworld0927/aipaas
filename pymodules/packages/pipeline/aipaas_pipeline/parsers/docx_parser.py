"""#19 Word 解析（python-docx）。

还原标题层级、条款编号、表格与列表；修订/批注按 ``revision_mode`` / ``comment_mode`` 处理。
``.doc`` 为 OLE 二进制，优先调用本机 LibreOffice 转换，缺失时给出明确失败码。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.errors import FailureCodes, ParseFailure
from aipaas_pipeline.models import Clause, ParsedDocument, SectionNode, TableBlock, WarningItem
from aipaas_pipeline.normalize import (
    chapter_path,
    extract_standard_no,
    infer_title,
    is_table_caption,
    make_section_id,
    match_heading,
    normalize_inline,
    normalize_text,
    table_caption_text,
)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_HEADING_STYLE_RE = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.IGNORECASE)
_LIST_STYLE_RE = re.compile(r"list|列表", re.IGNORECASE)
_LIST_LEVEL_RE = re.compile(r"(?:list|列表)\s*(?:number|bullet|paragraph|段落)?\s*([1-9])$", re.IGNORECASE)


class DocxParser:
    """Word（docx；doc 经 LibreOffice 转换后）解析器。"""

    name = "docx"
    formats = ("docx", "doc")

    def parse(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        if not data:
            raise ParseFailure(FailureCodes.EMPTY_DOCUMENT, "Word 内容为空", retryable=False)
        if data.startswith(_OLE_MAGIC):
            data = self._convert_legacy_doc(data, ctx)
        try:
            document = DocxDocument(BytesIO(data))
        except (PackageNotFoundError, KeyError, ValueError) as exc:
            raise ParseFailure(
                FailureCodes.DOCX_CORRUPTED, f"Word 文件无法打开：{str(exc)[:300]}", retryable=True
            ) from exc
        except Exception as exc:
            raise ParseFailure(
                FailureCodes.DOCX_CORRUPTED, f"Word 文件结构异常：{str(exc)[:300]}", retryable=True
            ) from exc
        return self._parse_document(document, ctx)

    # ------------------------------------------------------------------ 主流程

    def _parse_document(self, document: DocxDocument, ctx: ParseContext) -> ParsedDocument:
        options = ctx.options
        comments = self._load_comments(document)
        warnings: list[WarningItem] = []
        revision_count = 0

        roots: list[SectionNode] = []
        node_stack: list[SectionNode] = []
        path_stack: list[tuple[str, str]] = []
        clauses: list[Clause] = []
        tables: list[TableBlock] = []
        lists: list[dict[str, object]] = []
        raw_lines: list[str] = []
        section_index = 0
        current: Clause | None = None
        buffer: list[str] = []
        last_paragraph_text = ""

        def flush_clause() -> None:
            nonlocal current, buffer
            if current is not None:
                current.text = "\n".join(part for part in buffer if part).strip()
                clauses.append(current)
                current = None
                buffer = []

        body = document.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                text, revisions, comment_ids = self._paragraph_text(paragraph, ctx, comments)
                revision_count += revisions
                text = normalize_inline(text) if "\n" not in text else normalize_text(text)
                if not text:
                    continue
                raw_lines.append(text)

                list_level = self._list_level(paragraph)
                if list_level is not None:
                    lists.append(
                        {
                            "text": text,
                            "level": list_level,
                            "ordered": "number" in (paragraph.style.name or "").lower(),
                        }
                    )

                heading_level = self._heading_level(paragraph)
                heading = match_heading(text, max_level=options.max_heading_level)
                if heading is None and heading_level:
                    heading = _Heading(heading_level, text)
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
                        clause_no=getattr(heading, "clause_no", None) or None,
                        page_no=1,
                    )
                    if node_stack:
                        node_stack[-1].children.append(node)
                    else:
                        roots.append(node)
                    node_stack.append(node)
                    path_stack.append((getattr(heading, "clause_no", ""), heading.title))
                    if getattr(heading, "clause_no", None):
                        current = Clause(
                            clause_no=heading.clause_no,
                            title=heading.title,
                            text="",
                            chapter_path=chapter_path(path_stack),
                            level=heading.level,
                            page_no=1,
                            section_id=section_id,
                        )
                    if options.comment_mode == "keep" and comment_ids:
                        marker = self._append_comments("", comment_ids, comments)
                        if marker:
                            buffer.append(marker)
                            raw_lines.append(marker)
                    last_paragraph_text = text
                    continue

                if options.comment_mode == "keep" and comment_ids:
                    text = self._append_comments(text, comment_ids, comments)
                if current is not None:
                    if not is_table_caption(text):
                        buffer.append(text)
                last_paragraph_text = text

            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                rows = [
                    [normalize_inline(cell.text or "").replace("\n", " ") for cell in row.cells]
                    for row in table.rows
                ]
                rows = [row for row in rows if any(cell for cell in row)]
                if not rows:
                    warnings.append(
                        WarningItem(
                            "PARSE.EMPTY_TABLE",
                            "跳过空表格",
                            page_no=1,
                            element=f"table[{len(tables) + 1}]",
                        )
                    )
                    continue
                caption = table_caption_text(last_paragraph_text) if is_table_caption(last_paragraph_text) else None
                tables.append(
                    TableBlock(
                        table_id=f"tbl_docx_{len(tables) + 1}",
                        page_no=1,
                        rows=rows,
                        caption=caption,
                        header_rows=1,
                    )
                )

        flush_clause()
        raw_text = normalize_text("\n".join(raw_lines))
        if not raw_text:
            raise ParseFailure(FailureCodes.EMPTY_DOCUMENT, "Word 未包含可识别文本", retryable=False)

        standard_no = ctx.standard_no_hint or extract_standard_no(raw_text[:8000])
        title = ""
        try:
            title = normalize_inline(document.core_properties.title or "")
        except Exception:
            title = ""
        title = title or infer_title(raw_lines, standard_no)

        if revision_count:
            warnings.append(
                WarningItem(
                    "PARSE.REVISION_POLICY",
                    f"检测到 {revision_count} 处修订，策略={options.revision_mode}",
                    element="paragraphs",
                )
            )
        if comments and options.comment_mode == "ignore":
            warnings.append(
                WarningItem(
                    "PARSE.COMMENTS_IGNORED",
                    f"文档含 {len(comments)} 条批注，已按配置忽略",
                    element="comments",
                )
            )
        if options.comment_mode == "keep" and comments:
            warnings.append(
                WarningItem(
                    "PARSE.COMMENTS_RETAINED",
                    f"文档含 {len(comments)} 条批注，已保留到正文",
                    element="comments",
                )
            )

        return ParsedDocument(
            doc_id=ctx.doc_id,
            tenant_id=ctx.tenant_id,
            parser=self.name,
            success=True,
            title=title,
            standard_no=standard_no,
            chapter_tree=roots,
            clauses=clauses,
            tables=tables,
            raw_text=raw_text,
            warnings=warnings,
            pages=1,
            extras={
                "lists": lists,
                "commentCount": len(comments),
                "revisionCount": revision_count,
                "revisionMode": options.revision_mode,
                "commentMode": options.comment_mode,
            },
        )

    # ------------------------------------------------------------------ 修订/批注

    def _paragraph_text(
        self,
        paragraph: Paragraph,
        ctx: ParseContext,
        comments: dict[str, tuple[str, str]],
    ) -> tuple[str, int, list[str]]:
        keep_revision = ctx.options.revision_mode == "keep"
        pieces: list[str] = []
        revisions = 0

        def walk(element) -> None:
            nonlocal revisions
            for node in element:
                tag = node.tag
                if tag == qn("w:ins"):
                    inner = "".join(text.text or "" for text in node.iter(qn("w:t")))
                    if inner:
                        revisions += 1
                        pieces.append(f"⟦插入:{inner}⟧" if keep_revision else inner)
                elif tag == qn("w:del"):
                    inner = "".join(text.text or "" for text in node.iter(qn("w:delText")))
                    if inner:
                        revisions += 1
                        if keep_revision:
                            pieces.append(f"⟦删除:{inner}⟧")
                elif tag == qn("w:r"):
                    for item in node:
                        if item.tag == qn("w:t"):
                            pieces.append(item.text or "")
                        elif item.tag == qn("w:tab"):
                            pieces.append("\t")
                        elif item.tag in (qn("w:br"), qn("w:cr")):
                            pieces.append("\n")
                elif tag in (qn("w:hyperlink"), qn("w:smartTag"), qn("w:sdt"), qn("w:sdtContent")):
                    walk(node)

        walk(paragraph._p)
        comment_ids: list[str] = []
        if ctx.options.comment_mode == "keep":
            for start in paragraph._p.iter(qn("w:commentRangeStart")):
                identifier = start.get(qn("w:id"))
                if identifier and identifier in comments:
                    comment_ids.append(identifier)
        return "".join(pieces).strip(), revisions, comment_ids

    @staticmethod
    def _append_comments(text: str, comment_ids: list[str], comments: dict[str, tuple[str, str]]) -> str:
        rendered = []
        for identifier in comment_ids:
            author, body = comments[identifier]
            rendered.append(f"⟦批注:{author}:{body}⟧")
        return (text + " " + " ".join(rendered)).strip() if rendered else text

    @staticmethod
    def _load_comments(document: DocxDocument) -> dict[str, tuple[str, str]]:
        result: dict[str, tuple[str, str]] = {}
        try:
            for part in document.part.package.iter_parts():
                name = str(getattr(part, "partname", ""))
                if not name.endswith("comments.xml"):
                    continue
                root = etree.fromstring(part.blob)
                for comment in root.iter(qn("w:comment")):
                    identifier = comment.get(qn("w:id"))
                    if identifier is None:
                        continue
                    author = comment.get(qn("w:author")) or "unknown"
                    body = "".join(text.text or "" for text in comment.iter(qn("w:t"))).strip()
                    result[identifier] = (author, body)
        except Exception:
            # 批注部件缺失或损坏不影响正文解析，由调用方在 warnings 中体现数量为 0。
            return {}
        return result

    # ------------------------------------------------------------------ 标题/列表

    @staticmethod
    def _heading_level(paragraph: Paragraph) -> int:
        style = (paragraph.style.name or "").strip()
        match = _HEADING_STYLE_RE.match(style)
        if match:
            return int(match.group(1))
        if style.lower() == "title":
            return 1
        p_pr = paragraph._p.pPr
        if p_pr is not None and p_pr.outlineLvl is not None:
            value = p_pr.outlineLvl.val
            if value is not None and 0 <= int(value) < 9:
                return int(value) + 1
        return 0

    @staticmethod
    def _list_level(paragraph: Paragraph) -> int | None:
        style = paragraph.style.name or ""
        p_pr = paragraph._p.pPr
        numbered = p_pr is not None and p_pr.numPr is not None
        if not numbered and not _LIST_STYLE_RE.search(style):
            return None
        if numbered:
            num_pr = p_pr.numPr
            try:
                if num_pr.ilvl is not None and num_pr.ilvl.val is not None:
                    return int(num_pr.ilvl.val)
            except (AttributeError, TypeError, ValueError):
                pass
        suffix = _LIST_LEVEL_RE.search(style.strip())
        if suffix:
            return int(suffix.group(1)) - 1
        return 0

    # ------------------------------------------------------------------ doc 转换

    @staticmethod
    def _convert_legacy_doc(data: bytes, ctx: ParseContext) -> bytes:
        binary = shutil.which("soffice") or shutil.which("libreoffice")
        if not binary:
            raise ParseFailure(
                FailureCodes.DOC_LEGACY_UNSUPPORTED,
                "检测到旧版 .doc（OLE）文件；当前运行环境缺少 LibreOffice，无法转换。"
                "请上传 .docx 或在镜像中安装 libreoffice。",
                retryable=False,
                element="file",
            )
        with tempfile.TemporaryDirectory(prefix="aipaas-doc-") as workdir:
            source = Path(workdir) / "source.doc"
            source.write_bytes(data)
            try:
                subprocess.run(
                    [binary, "--headless", "--convert-to", "docx", "--outdir", workdir, str(source)],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                raise ParseFailure(
                    FailureCodes.DOC_LEGACY_UNSUPPORTED,
                    f".doc 转换失败：{str(exc)[:300]}",
                    retryable=True,
                    element="file",
                ) from exc
            converted = Path(workdir) / "source.docx"
            if not converted.exists():
                raise ParseFailure(
                    FailureCodes.DOC_LEGACY_UNSUPPORTED,
                    ".doc 转换未生成 docx 文件",
                    retryable=False,
                    element="file",
                )
            return converted.read_bytes()


class _Heading:
    """轻量标题对象，兼容 ``match_heading`` 的字段访问。"""

    __slots__ = ("level", "title", "clause_no", "kind")

    def __init__(self, level: int, title: str) -> None:
        self.level = level
        self.title = title
        self.clause_no = None
        self.kind = "font"
