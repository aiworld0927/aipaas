"""#14 数据清洗基础：乱码/非法字符/空行/重复空白清理。

P0 边界：只保证“可分块、可入库”，不做全半角、编码（UTF-8/GBK）、目录结构规范化
（这些归 P1 #258）。清洗规则全部可配置，并且：
- 条款号、章节 id/层级永不改写；
- 表格单元格（公式）、代码块、rawText 中的公式行做保护，不被误删；
- 每个字段的变更写入 diff 报告，清洗结果作为新产物写入同一解析版本，不覆盖原始解析结果。
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from aipaas_pipeline.models import Clause, ParsedDocument, SectionNode, TableBlock

RULES_VERSION = "p0-clean-v1"

# 已知 mojibake 片段（UTF-8 被按 Latin-1/GBK 误解码后的常见形态），只做精确替换
_MOJIBAKE_TOKENS = (
    "â€™", "â€˜", "â€œ", "â€\x9d", "â€“", "â€”", "â€¦", "Â ", "Ã©", "Ã¨", "Ã¼",
    "ï¼Œ", "ï¼š", "ï¼›", "ï¼ˆ", "ï¼‰", "ï¼�", "ï¼›", "ã€‚", "ã€", "ã€‹", "ã€Š",
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class CleaningOptions:
    """清洗配置：默认值面向金融标准文本，允许按租户/文档覆盖。"""

    enabled: bool = True
    remove_control_chars: bool = True
    remove_zero_width: bool = True
    remove_replacement_chars: bool = True
    remove_mojibake_tokens: bool = True
    normalize_newlines: bool = True
    trim_line_edges: bool = True
    collapse_inline_spaces: bool = True
    collapse_blank_lines: bool = True
    max_consecutive_blank_lines: int = 1
    protect_tables: bool = True
    protect_code_blocks: bool = True
    protect_formulas: bool = True
    max_diff_entries: int = 500

    def __post_init__(self) -> None:
        if self.max_consecutive_blank_lines < 0:
            raise ValueError("max_consecutive_blank_lines 必须 ≥0")
        if self.max_diff_entries < 1:
            raise ValueError("max_diff_entries 必须 ≥1")

    def to_dict(self) -> dict[str, Any]:
        return {field_name: getattr(self, field_name) for field_name in self.__dataclass_fields__}

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "CleaningOptions":
        if not values:
            return cls()
        bool_fields = {
            "enabled", "remove_control_chars", "remove_zero_width", "remove_replacement_chars",
            "remove_mojibake_tokens", "normalize_newlines", "trim_line_edges",
            "collapse_inline_spaces", "collapse_blank_lines", "protect_tables",
            "protect_code_blocks", "protect_formulas",
        }
        filtered: dict[str, Any] = {}
        defaults = cls()
        for key, value in values.items():
            if value is None or key not in cls.__dataclass_fields__:
                continue
            filtered[key] = _as_bool(value, getattr(defaults, key)) if key in bool_fields else value
        return cls(**filtered)


@dataclass(slots=True)
class CleaningResult:
    document: ParsedDocument
    diff: dict[str, Any]

    def clean_payload(self) -> dict[str, Any]:
        payload = self.document.to_payload()
        payload["extras"] = {
            **(payload.get("extras") or {}),
            "cleaning": {
                "rulesVersion": RULES_VERSION,
                "applied": self.document.extras.get("cleaningApplied", True),
                "summary": self.diff["summary"],
                "diffKey": self.document.extras.get("cleanDiffKey"),
            },
        }
        return payload


class _DiffCollector:
    def __init__(self, options: CleaningOptions) -> None:
        self.options = options
        self.changes: list[dict[str, Any]] = []
        self.total_changed_fields = 0
        self.removed_chars = 0
        self.removed_blank_lines = 0
        self.raw_lines_before = 0
        self.raw_lines_after = 0

    def record(self, path: str, before: str, after: str, applied: list[str], *, element: str) -> None:
        if before == after:
            return
        removed = max(0, len(before) - len(after))
        self.total_changed_fields += 1
        self.removed_chars += removed
        if len(self.changes) >= self.options.max_diff_entries:
            return
        self.changes.append(
            {
                "path": path,
                "element": element,
                "rules": applied,
                "removedChars": removed,
                "before": _snippet(before),
                "after": _snippet(after),
            }
        )

    def payload(self, invariants: dict[str, Any], protection: dict[str, int]) -> dict[str, Any]:
        summary = {
            "rulesVersion": RULES_VERSION,
            "changedFields": self.total_changed_fields,
            "removedChars": self.removed_chars,
            "removedBlankLines": self.removed_blank_lines,
            "rawTextLinesBefore": self.raw_lines_before,
            "rawTextLinesAfter": self.raw_lines_after,
            "diffEntries": len(self.changes),
            "diffTruncated": self.total_changed_fields > len(self.changes),
            **protection,
        }
        return {
            "rulesVersion": RULES_VERSION,
            "rules": self.options.to_dict(),
            "summary": summary,
            "invariants": invariants,
            "changes": self.changes,
        }


def clean_document(document: ParsedDocument, options: CleaningOptions | None = None) -> CleaningResult:
    """清洗解析产物，返回新文档与差异报告；入参文档不会被修改。"""
    options = options or CleaningOptions()
    if not options.enabled or not document.success:
        clone = copy.deepcopy(document)
        clone.extras["cleaningApplied"] = False
        diff = {
            "rulesVersion": RULES_VERSION,
            "rules": options.to_dict(),
            "summary": {"changedFields": 0, "removedChars": 0, "diffEntries": 0,
                        "cleaningApplied": False},
            "invariants": _invariants(document, clone, options),
            "changes": [],
        }
        return CleaningResult(clone, diff)

    cleaned = copy.deepcopy(document)
    diff = _DiffCollector(options)
    protection = {"protectedTableCells": 0, "protectedCodeBlocks": 0, "protectedFormulaLines": 0}

    cleaned.title, _ = _record_value("title", document.title, diff, options, element="title")
    cleaned.standard_no = document.standard_no
    _clean_sections(document.chapter_tree, cleaned.chapter_tree, "", diff, options)
    _clean_clauses(document.clauses, cleaned.clauses, diff, options)
    _clean_tables(document.tables, cleaned.tables, diff, options, protection)
    cleaned.raw_text = _clean_raw_text(document.raw_text, diff, options, protection)

    code_blocks = cleaned.extras.get("codeBlocks")
    if code_blocks:
        protection["protectedCodeBlocks"] = len(code_blocks)

    invariants = _invariants(document, cleaned, options)
    payload = diff.payload(invariants, protection)
    cleaned.extras["cleaningApplied"] = True
    cleaned.extras["cleaning"] = payload["summary"]
    return CleaningResult(cleaned, payload)


# --------------------------------------------------------------------------- 结构清洗

def _clean_sections(
    before: list[SectionNode], after: list[SectionNode], prefix: str, diff: _DiffCollector,
    options: CleaningOptions,
) -> None:
    for index, (source, target) in enumerate(zip(before, after)):
        path = f"{prefix}chapterTree[{index}]"
        target.title, _ = _record_value(f"{path}.title", source.title, diff, options, element="sectionTitle")
        _clean_sections(source.children, target.children, path + ".", diff, options)


def _clean_clauses(before: list[Clause], after: list[Clause], diff: _DiffCollector,
                   options: CleaningOptions) -> None:
    for index, (source, target) in enumerate(zip(before, after)):
        path = f"clauses[{index}]"
        target.clause_no = source.clause_no
        target.title, _ = _record_value(f"{path}.title", source.title, diff, options, element="clauseTitle")
        target.text, _ = _record_value(f"{path}.text", source.text, diff, options, element="clauseText")
        target.chapter_path, _ = _record_value(
            f"{path}.chapterPath", source.chapter_path, diff, options, element="chapterPath"
        )


def _clean_tables(before: list[TableBlock], after: list[TableBlock], diff: _DiffCollector,
                  options: CleaningOptions, protection: dict[str, int]) -> None:
    for table_index, (source, target) in enumerate(zip(before, after)):
        target.table_id = source.table_id
        target.page_no = source.page_no
        if source.caption:
            target.caption, _ = _record_value(
                f"tables[{table_index}].caption", source.caption, diff, options, element="tableCaption"
            )
        for row_index, (source_row, target_row) in enumerate(zip(source.rows, target.rows)):
            for cell_index, cell in enumerate(source_row):
                if options.protect_tables and options.protect_formulas and _is_formula(cell):
                    target_row[cell_index] = cell
                    protection["protectedTableCells"] += 1
                    continue
                target_row[cell_index], _ = _record_value(
                    f"tables[{table_index}].rows[{row_index}][{cell_index}]", cell, diff, options,
                    element="tableCell",
                )


def _clean_raw_text(text: str, diff: _DiffCollector, options: CleaningOptions,
                    protection: dict[str, int]) -> str:
    if not text:
        return text
    if options.normalize_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    source_lines = text.split("\n")
    diff.raw_lines_before = len(source_lines)
    output: list[str] = []
    in_fence = False
    in_fence_buffer: list[str] = []
    blank_run = 0
    changed = 0

    def flush_fence() -> None:
        nonlocal in_fence_buffer
        output.extend(in_fence_buffer)
        in_fence_buffer = []

    for line in source_lines:
        if _FENCE_RE.match(line):
            if options.protect_code_blocks:
                if in_fence:
                    in_fence_buffer.append(line)
                    flush_fence()
                else:
                    in_fence_buffer.append(line)
                in_fence = not in_fence
                blank_run = 0
                continue
        if in_fence:
            in_fence_buffer.append(line)
            continue
        if options.protect_formulas and _line_has_formula(line):
            output.append(line)
            protection["protectedFormulaLines"] += 1
            blank_run = 0
            continue
        cleaned, _ = _clean_text(line, options)
        if cleaned != line:
            changed += 1
            if cleaned.strip() == "" and line.strip() == "":
                diff.removed_blank_lines += 1
            elif changed <= 10:
                diff.record("rawText", line, cleaned, ["normalize_whitespace"], element="rawTextLine")
        if cleaned == "" and options.collapse_blank_lines:
            blank_run += 1
            if blank_run > options.max_consecutive_blank_lines:
                diff.removed_blank_lines += 1
                continue
        else:
            blank_run = 0
        output.append(cleaned)
    if in_fence and in_fence_buffer:
        flush_fence()
    diff.raw_lines_after = len(output)
    return "\n".join(output)


# --------------------------------------------------------------------------- 文本规则

def _record_value(path: str, value: str, diff: _DiffCollector, options: CleaningOptions, *,
                  element: str) -> tuple[str, list[str]]:
    cleaned, applied = _clean_text(value, options)
    diff.record(path, value, cleaned, applied, element=element)
    return cleaned, applied


def _clean_text(text: str, options: CleaningOptions) -> tuple[str, list[str]]:
    if not text:
        return text, []
    applied: list[str] = []
    value = text
    if options.normalize_newlines and "\r" in value:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        applied.append("normalize_newlines")
    if options.remove_zero_width:
        updated = _ZERO_WIDTH_RE.sub("", value)
        if updated != value:
            value = updated
            applied.append("remove_zero_width")
    if options.remove_control_chars:
        updated = _CONTROL_RE.sub("", value)
        if updated != value:
            value = updated
            applied.append("remove_control_chars")
    if options.remove_replacement_chars and "\ufffd" in value:
        value = value.replace("\ufffd", "")
        applied.append("remove_replacement_chars")
    if options.remove_mojibake_tokens:
        before_mojibake = value
        for token in _MOJIBAKE_TOKENS:
            if token in value:
                value = value.replace(token, "")
        if value != before_mojibake:
            applied.append("remove_mojibake_tokens")
    if options.trim_line_edges:
        updated = "\n".join(line.strip(" \t") for line in value.split("\n"))
        if updated != value:
            value = updated
            applied.append("trim_line_edges")
    if options.collapse_inline_spaces:
        updated = _MULTI_SPACE_RE.sub(" ", value)
        if updated != value:
            value = updated
            applied.append("collapse_inline_spaces")
    if options.collapse_blank_lines:
        updated = value.strip("\n")
        if updated != value:
            value = updated
            applied.append("collapse_blank_lines")
    return value, applied


def _is_formula(value: str) -> bool:
    return value.lstrip().startswith("=") and len(value.strip()) > 1


def _line_has_formula(line: str) -> bool:
    if _is_formula(line):
        return True
    return any(_is_formula(part) for part in line.split("\t") if part.strip())


# --------------------------------------------------------------------------- 不变量

def _flatten_sections(nodes: list[SectionNode]) -> list[tuple[str, int, str | None]]:
    flat: list[tuple[str, int, str | None]] = []
    for node in nodes:
        flat.append((node.section_id, node.level, node.clause_no))
        flat.extend(_flatten_sections(node.children))
    return flat


def _invariants(before: ParsedDocument, after: ParsedDocument, options: CleaningOptions) -> dict[str, Any]:
    before_sections = _flatten_sections(before.chapter_tree)
    after_sections = _flatten_sections(after.chapter_tree)
    before_clauses = [(clause.clause_no, clause.level) for clause in before.clauses]
    after_clauses = [(clause.clause_no, clause.level) for clause in after.clauses]
    before_tables = [(table.table_id, len(table.rows)) for table in before.tables]
    after_tables = [(table.table_id, len(table.rows)) for table in after.tables]
    before_code = [str(block.get("content", "")) for block in before.extras.get("codeBlocks", []) or []]
    after_code = [str(block.get("content", "")) for block in after.extras.get("codeBlocks", []) or []]
    return {
        "clauseCount": len(after_clauses),
        "clauseNumbersPreserved": before_clauses == after_clauses,
        "sectionTreePreserved": before_sections == after_sections,
        "tableShapePreserved": before_tables == after_tables,
        "codeBlocksPreserved": before_code == after_code,
        "codeBlocksProtected": options.protect_code_blocks,
    }


def _snippet(text: str, limit: int = 160) -> str:
    value = text.replace("\n", "\\n")
    return value if len(value) <= limit else value[: limit - 1] + "…"
