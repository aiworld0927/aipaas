"""#21 Markdown 解析（markdown-it-py）。

解析标题/列表/代码块/表格/引用；代码块按原样保留且不参与条款正文拼接，避免被混切。
"""

from __future__ import annotations

import re

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
    table_caption_text,
)


class MarkdownParser:
    """CommonMark + GFM 表格解析器。"""

    name = "markdown"
    formats = ("md", "markdown")

    def parse(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        if not data:
            raise ParseFailure(FailureCodes.EMPTY_DOCUMENT, "Markdown 内容为空", retryable=False)
        warnings: list[WarningItem] = []
        text, fallback = self._decode(data)
        if fallback:
            warnings.append(
                WarningItem(
                    "PARSE.ENCODING_FALLBACK",
                    f"UTF-8 解码失败，已按 {fallback} 解码",
                    element="file",
                )
            )
        raw_text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not raw_text.strip():
            raise ParseFailure(
                FailureCodes.EMPTY_DOCUMENT, "Markdown 未包含有效内容", retryable=False, warnings=warnings
            )

        from markdown_it import MarkdownIt

        parser = MarkdownIt("commonmark").enable("table")
        try:
            # CommonMark 会把 "4.1.1 标题" 误判为有序列表（标记 4. + 内容 1.1 ...）。
            # 转义首个点后仍保持行号与显示文本不变，但不会破坏条款编号。
            tokens = parser.parse(_protect_clause_lines(raw_text))
        except Exception as exc:
            raise ParseFailure(
                FailureCodes.MARKDOWN_CORRUPTED, f"Markdown 解析失败：{str(exc)[:300]}", retryable=True
            ) from exc

        roots: list[SectionNode] = []
        node_stack: list[SectionNode] = []
        path_stack: list[tuple[str, str]] = []
        clauses: list[Clause] = []
        tables: list[TableBlock] = []
        lists: list[dict[str, object]] = []
        code_blocks: list[dict[str, object]] = []
        section_index = 0
        current: Clause | None = None
        buffer: list[str] = []
        last_paragraph = ""

        def flush_clause() -> None:
            nonlocal current, buffer
            if current is not None:
                current.text = "\n".join(part for part in buffer if part).strip()
                clauses.append(current)
                current = None
                buffer = []

        in_table = False
        in_header = False
        table_rows: list[list[str]] = []
        current_row: list[str] | None = None
        current_cell: list[str] | None = None
        table_header_rows = 0
        list_stack: list[str] = []
        blockquote_depth = 0

        index = 0
        while index < len(tokens):
            token = tokens[index]

            if token.type == "heading_open":
                level = int(token.tag[1]) if token.tag.startswith("h") and token.tag[1:].isdigit() else 1
                inline = tokens[index + 1] if index + 1 < len(tokens) else None
                title = normalize_inline(inline.content if inline is not None else "")
                if title:
                    flush_clause()
                    section_index += 1
                    section_id = make_section_id(section_index)
                    while node_stack and node_stack[-1].level >= level:
                        node_stack.pop()
                    while path_stack and len(path_stack) >= level:
                        path_stack.pop()
                    match = match_heading(title, max_level=ctx.options.max_heading_level)
                    node = SectionNode(
                        section_id=section_id,
                        title=title,
                        level=level,
                        clause_no=match.clause_no if match else None,
                        page_no=1,
                    )
                    if node_stack:
                        node_stack[-1].children.append(node)
                    else:
                        roots.append(node)
                    node_stack.append(node)
                    path_stack.append((match.clause_no if match else "", title))
                    if match:
                        current = Clause(
                            clause_no=match.clause_no,
                            title=match.title,
                            text="",
                            chapter_path=chapter_path(path_stack),
                            level=match.level,
                            page_no=1,
                            section_id=section_id,
                        )
                    last_paragraph = title
                index += 3
                continue

            if token.type in {"fence", "code_block"}:
                language = (token.info or "").strip().split(" ")[0]
                start_line, end_line = (token.map or [0, 0])[0] + 1, (token.map or [0, 0])[1]
                code_blocks.append(
                    {
                        "language": language,
                        "content": token.content,
                        "startLine": start_line,
                        "endLine": end_line,
                    }
                )
                index += 1
                continue

            if token.type == "table_open":
                in_table = True
                table_rows = []
                table_header_rows = 0
                index += 1
                continue
            if token.type == "thead_open":
                in_header = True
                table_header_rows = 1
                index += 1
                continue
            if token.type == "thead_close":
                in_header = False
                index += 1
                continue
            if token.type == "tr_open":
                current_row = []
                index += 1
                continue
            if token.type == "tr_close":
                if current_row:
                    table_rows.append(current_row)
                current_row = None
                index += 1
                continue
            if token.type in {"th_open", "td_open"}:
                current_cell = []
                index += 1
                continue
            if token.type in {"th_close", "td_close"}:
                if current_row is not None:
                    current_row.append(normalize_inline(" ".join(current_cell or [])))
                current_cell = None
                index += 1
                continue
            if token.type == "table_close":
                if table_rows:
                    caption = table_caption_text(last_paragraph) if is_table_caption(last_paragraph) else None
                    tables.append(
                        TableBlock(
                            table_id=f"tbl_md_{len(tables) + 1}",
                            page_no=1,
                            rows=table_rows,
                            caption=caption,
                            header_rows=table_header_rows,
                        )
                    )
                in_table = False
                table_rows = []
                index += 1
                continue

            if token.type in {"bullet_list_open", "ordered_list_open"}:
                list_stack.append("ordered" if token.type == "ordered_list_open" else "bullet")
                index += 1
                continue
            if token.type in {"bullet_list_close", "ordered_list_close"}:
                if list_stack:
                    list_stack.pop()
                index += 1
                continue
            if token.type == "blockquote_open":
                blockquote_depth += 1
                index += 1
                continue
            if token.type == "blockquote_close":
                blockquote_depth = max(0, blockquote_depth - 1)
                index += 1
                continue

            if token.type == "inline" and in_table:
                if current_cell is not None:
                    current_cell.append(_unescape_markdown(token.content))
                index += 1
                continue

            if token.type == "inline" and not in_table:
                raw_body = _unescape_markdown(token.content)
                body = normalize_inline(raw_body)
                if not body:
                    index += 1
                    continue
                first_line, _, remainder = raw_body.partition("\n")
                heading_text = normalize_inline(first_line)
                if list_stack:
                    lists.append(
                        {
                            "text": body,
                            "level": len(list_stack) - 1,
                            "ordered": list_stack[-1] == "ordered",
                        }
                    )
                rendered = f"{'> ' * blockquote_depth}{body}"
                heading = match_heading(heading_text, max_level=ctx.options.max_heading_level)
                if heading is not None and len(heading_text) <= 200 and not list_stack:
                    flush_clause()
                    section_index += 1
                    section_id = make_section_id(section_index)
                    node = SectionNode(
                        section_id=section_id,
                        title=heading.title,
                        level=heading.level,
                        clause_no=heading.clause_no,
                        page_no=1,
                    )
                    parent = node_stack[-1] if node_stack else None
                    if parent is not None:
                        parent.children.append(node)
                    else:
                        roots.append(node)
                    while path_stack and len(path_stack) >= heading.level:
                        path_stack.pop()
                    path_stack.append((heading.clause_no, heading.title))
                    current = Clause(
                        clause_no=heading.clause_no,
                        title=heading.title,
                        text="",
                        chapter_path=chapter_path(path_stack),
                        level=heading.level,
                        page_no=1,
                        section_id=section_id,
                    )
                    if remainder.strip():
                        buffer.append(remainder.strip())
                elif current is not None:
                    if is_table_caption(body):
                        last_paragraph = body
                        index += 1
                        continue
                    prefix = "- " if list_stack and list_stack[-1] == "bullet" else "1. " if list_stack else ""
                    buffer.append(f"{prefix}{rendered}")
                last_paragraph = body
            index += 1

        flush_clause()
        lines = raw_text.split("\n")
        standard_no = ctx.standard_no_hint or extract_standard_no(raw_text[:8000])
        title = next((node.title for node in roots), "") or infer_title(lines, standard_no)
        if standard_no and title.upper().startswith(standard_no.upper()):
            title = title[len(standard_no):].lstrip(" ：:—-") or title
        if not clauses:
            warnings.append(
                WarningItem("PARSE.NO_CLAUSE", "未识别到带编号条款，已保留标题树与原文", element="clauses")
            )
        if code_blocks:
            warnings.append(
                WarningItem(
                    "PARSE.CODE_BLOCK_ATOMIC",
                    f"保留 {len(code_blocks)} 个代码块，分块时将作为整体不再切分",
                    element="codeBlocks",
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
                "codeBlocks": code_blocks,
                "blockquoteMaxDepth": blockquote_depth,
            },
        )

    @staticmethod
    def _decode(data: bytes) -> tuple[str, str | None]:  # noqa: D401
        try:
            return data.decode("utf-8-sig"), None
        except UnicodeDecodeError:
            pass
        for encoding in ("gb18030", "gbk"):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        raise ParseFailure(FailureCodes.MARKDOWN_CORRUPTED, "Markdown 编码无法识别", retryable=False)


_CLAUSE_LINE_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){1,5})([ \t\u3000]+)")
_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")


def _unescape_markdown(text: str) -> str:
    """还原 markdown-it token.content 中保留的反斜杠转义。"""
    return _ESCAPE_RE.sub(r"\1", text)


def _protect_clause_lines(text: str) -> str:
    """把行首条款号的首个点转义，避免被 markdown-it 解析为有序列表。"""
    lines = text.split("\n")
    in_fence = False
    result: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            result.append(line)
            continue
        if not in_fence:
            match = _CLAUSE_LINE_RE.match(line)
            if match:
                number = match.group(1)
                protected = number.replace(".", r"\.", 1)
                line = protected + line[len(number):]
        result.append(line)
    return "\n".join(result)
