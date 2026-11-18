"""统一中间结构：契约 ParseResult + 业务扩展字段。

固定键：``docId, standardNo, title, chapterTree, clauses[], tables[], rawText, warnings``。
同时输出契约字段：``documentId, sections, clauseAnchors, parser, success, pages`` 等。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class WarningItem:
    code: str
    message: str
    page_no: int | None = None
    element: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.page_no is not None:
            payload["pageNo"] = self.page_no
        if self.element:
            payload["element"] = self.element
        return payload


@dataclass(slots=True)
class SectionNode:
    section_id: str
    title: str
    level: int
    clause_no: str | None = None
    page_no: int | None = None
    children: list["SectionNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sectionId": self.section_id,
            "title": self.title,
            "level": self.level,
        }
        if self.clause_no:
            payload["clauseNo"] = self.clause_no
        if self.page_no is not None:
            payload["pageNo"] = self.page_no
        if self.children:
            payload["children"] = [child.to_dict() for child in self.children]
        return payload


@dataclass(slots=True)
class TableBlock:
    table_id: str
    page_no: int
    rows: list[list[str]]
    caption: str | None = None
    header_rows: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tableId": self.table_id,
            "pageNo": max(1, self.page_no),
            "headerRows": self.header_rows,
            "rows": self.rows,
        }
        if self.caption:
            payload["caption"] = self.caption
        return payload


@dataclass(slots=True)
class Clause:
    clause_no: str
    title: str
    text: str
    chapter_path: str
    level: int
    page_no: int | None = None
    section_id: str | None = None

    def to_anchor(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "clauseNo": self.clause_no,
            "title": self.title,
            "chapterPath": self.chapter_path,
            "level": self.level,
        }
        if self.page_no is not None:
            payload["pageNo"] = self.page_no
        return payload


@dataclass(slots=True)
class ParsedDocument:
    doc_id: str
    parser: str
    success: bool
    title: str
    chapter_tree: list[SectionNode]
    clauses: list[Clause]
    tables: list[TableBlock]
    raw_text: str
    warnings: list[WarningItem] = field(default_factory=list)
    standard_no: str | None = None
    pages: int = 0
    failure_code: str | None = None
    failure_message: str | None = None
    retry_count: int = 0
    version: int = 0
    tenant_id: str | None = None
    parsed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extras: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """输出统一中间结构（包含契约字段与扩展字段）。"""
        payload: dict[str, Any] = {
            "docId": self.doc_id,
            "documentId": self.doc_id,
            "standardNo": self.standard_no,
            "title": self.title,
            "chapterTree": [node.to_dict() for node in self.chapter_tree],
            "sections": [node.to_dict() for node in self.chapter_tree],
            "clauses": [
                {
                    **clause.to_anchor(),
                    "text": clause.text,
                    **({"sectionId": clause.section_id} if clause.section_id else {}),
                }
                for clause in self.clauses
            ],
            "clauseAnchors": [clause.to_anchor() for clause in self.clauses],
            "tables": [table.to_dict() for table in self.tables],
            "rawText": self.raw_text,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "parser": self.parser,
            "success": self.success,
            "failureCode": self.failure_code,
            "retryCount": self.retry_count,
            "pages": self.pages,
            "version": self.version,
            "parsedAt": self.parsed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if self.tenant_id:
            payload["tenantId"] = self.tenant_id
        if self.failure_message:
            payload["failureMessage"] = self.failure_message
        if self.extras:
            payload["extras"] = self.extras
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ParsedDocument":
        """从持久化的统一中间结构还原文档对象（用于分块预览等只读场景）。"""
        parsed_at = payload.get("parsedAt")
        return cls(
            doc_id=payload.get("docId") or payload.get("documentId") or "",
            parser=payload.get("parser", ""),
            success=bool(payload.get("success", False)),
            title=payload.get("title", ""),
            standard_no=payload.get("standardNo"),
            chapter_tree=[_section_from_dict(node) for node in payload.get("chapterTree") or []],
            clauses=[
                Clause(
                    clause_no=item.get("clauseNo") or "",
                    title=item.get("title", ""),
                    text=item.get("text", ""),
                    chapter_path=item.get("chapterPath", ""),
                    level=int(item.get("level", 0) or 0),
                    page_no=item.get("pageNo"),
                    section_id=item.get("sectionId"),
                )
                for item in payload.get("clauses") or []
            ],
            tables=[
                TableBlock(
                    table_id=item.get("tableId", ""),
                    page_no=int(item.get("pageNo", 1) or 1),
                    rows=[list(row) for row in item.get("rows") or []],
                    caption=item.get("caption"),
                    header_rows=int(item.get("headerRows", 1) or 1),
                )
                for item in payload.get("tables") or []
            ],
            raw_text=payload.get("rawText", ""),
            warnings=[
                WarningItem(
                    code=item.get("code", ""),
                    message=item.get("message", ""),
                    page_no=item.get("pageNo"),
                    element=item.get("element"),
                )
                for item in payload.get("warnings") or []
            ],
            pages=int(payload.get("pages", 0) or 0),
            failure_code=payload.get("failureCode"),
            failure_message=payload.get("failureMessage"),
            retry_count=int(payload.get("retryCount", 0) or 0),
            version=int(payload.get("version", 0) or 0),
            tenant_id=payload.get("tenantId"),
            parsed_at=datetime.fromisoformat(parsed_at.replace("Z", "+00:00"))
            if parsed_at
            else datetime.now(timezone.utc),
            extras=dict(payload.get("extras") or {}),
        )


def _section_from_dict(payload: dict[str, Any]) -> SectionNode:
    return SectionNode(
        section_id=payload.get("sectionId", ""),
        title=payload.get("title", ""),
        level=int(payload.get("level", 1) or 1),
        clause_no=payload.get("clauseNo"),
        page_no=payload.get("pageNo"),
        children=[_section_from_dict(child) for child in payload.get("children") or []],
    )
