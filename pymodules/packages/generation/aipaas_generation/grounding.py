"""#106 幻觉抑制：只基于检索条款作答，引用与条款编号可对应。

策略：
- 答案中的 ``[n]`` 必须落在本次检索条款范围内，未知编号会被移除并告警；
- 引用结构由实际引用的条款生成（chunkId/documentId/clauseNo），缺字段的引用被丢弃；
- 无引用且含规范性断言（应当/必须/不得/...）的句子视为“无依据断言”：
  默认追加“未检索到直接依据”的不确定标注（``annotate``），也可配置为直接移除（``drop``）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from aipaas_generation.models import Citation, RetrievedClause

MARKER_PATTERN = re.compile(r"\[(\d{1,2})\]")
NORMATIVE_PATTERN = re.compile(
    r"(应当|必须|不得|禁止|严禁|应予以|须|shall|must|should|prohibited|required)", re.IGNORECASE
)
META_PATTERN = re.compile(r"(不足以|建议查阅|未检索到|无法回答|抱歉|没有找到|不确定|无法确定)")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")

MODE_ANNOTATE = "annotate"
MODE_DROP = "drop"
DEFAULT_ANNOTATION = "（未检索到直接依据，建议查阅原标准）"


@dataclass(slots=True)
class GroundingResult:
    content: str
    citations: list[Citation] = field(default_factory=list)
    cited_clause_ids: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    annotated_claims: int = 0
    dropped_claims: int = 0
    unknown_markers: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def citation_count(self) -> int:
        return len(self.citations)


class GroundingChecker:
    """按检索条款校验答案：引用可解析、无依据断言不直接输出。"""

    def __init__(
        self,
        *,
        mode: str = MODE_ANNOTATE,
        annotation: str = DEFAULT_ANNOTATION,
        max_claim_chars: int = 160,
    ) -> None:
        if mode not in {MODE_ANNOTATE, MODE_DROP}:
            raise ValueError("grounding mode 仅支持 annotate | drop")
        self.mode = mode
        self.annotation = annotation
        self.max_claim_chars = max_claim_chars

    def check(self, answer: str, clauses: Sequence[RetrievedClause]) -> GroundingResult:
        allowed = {index: clause for index, clause in enumerate(clauses, start=1)}
        text = answer or ""
        found_markers = {int(value) for value in MARKER_PATTERN.findall(text)}
        unknown = sorted(value for value in found_markers if value not in allowed)

        processed_lines: list[str] = []
        unsupported: list[str] = []
        annotated = 0
        dropped = 0
        for line in text.split("\n"):
            processed, line_annotated, line_dropped, line_unsupported = self._process_line(
                line, allowed
            )
            processed_lines.append(processed)
            annotated += line_annotated
            dropped += line_dropped
            unsupported.extend(line_unsupported)
        content = "\n".join(processed_lines)
        content = MARKER_PATTERN.sub(
            lambda match: "" if int(match.group(1)) not in allowed else match.group(0), content
        )

        citations = self._build_citations(content, allowed)
        warnings: list[str] = []
        if unknown:
            warnings.append(
                "CITATION_UNKNOWN_MARKER: 答案引用了不存在的条款编号 "
                + ",".join(str(value) for value in unknown)
                + "，已移除"
            )
        if annotated:
            warnings.append(f"UNSUPPORTED_CLAIM_ANNOTATED: {annotated} 处无依据断言已标注不确定")
        if dropped:
            warnings.append(f"UNSUPPORTED_CLAIM_DROPPED: {dropped} 处无依据断言已移除")
        if not citations:
            warnings.append("NO_CITATION: 答案未引用任何检索条款")
        return GroundingResult(
            content=content.strip(),
            citations=citations,
            cited_clause_ids=[citation.chunk_id for citation in citations],
            unsupported_claims=unsupported,
            annotated_claims=annotated,
            dropped_claims=dropped,
            unknown_markers=unknown,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ 内部

    def _process_line(
        self,
        line: str,
        allowed: dict[int, RetrievedClause],
    ) -> tuple[str, int, int, list[str]]:
        stripped = line.strip()
        if not stripped:
            return line, 0, 0, []
        # 标题与表格保持原样，避免破坏 Markdown 结构。
        if stripped.startswith("#") or stripped.startswith("|"):
            return line, 0, 0, []
        prefix, body = _split_markdown_prefix(line)
        if not body.strip():
            return line, 0, 0, []
        processed, annotated, dropped, unsupported = self._process_sentences(body, allowed)
        return prefix + processed, annotated, dropped, unsupported

    def _process_sentences(
        self,
        body: str,
        allowed: dict[int, RetrievedClause],
    ) -> tuple[str, int, int, list[str]]:
        # 仅处理正文行；保留句末标点，逐句判定是否具备可解析引用。
        parts = _merge_trailing_markers(_SENTENCE_SPLIT.split(body))
        output: list[str] = []
        annotated = 0
        dropped = 0
        unsupported: list[str] = []
        for part in parts:
            if not part:
                continue
            has_valid_marker = any(
                int(value) in allowed for value in MARKER_PATTERN.findall(part)
            )
            is_normative = bool(NORMATIVE_PATTERN.search(part))
            is_meta = bool(META_PATTERN.search(part))
            if has_valid_marker or not is_normative or is_meta:
                output.append(part)
                continue
            unsupported.append(_clip(part.strip(), self.max_claim_chars))
            if self.mode == MODE_DROP:
                dropped += 1
                continue
            output.append(f"{part}{self.annotation}")
            annotated += 1
        return "".join(output), annotated, dropped, unsupported

    @staticmethod
    def _build_citations(
        content: str, allowed: dict[int, RetrievedClause]
    ) -> list[Citation]:
        citations: list[Citation] = []
        seen: set[tuple[str, str | None]] = set()

        def add(clause: RetrievedClause) -> None:
            if not clause.chunk_id or not clause.document_id:
                return
            citation = clause.citation()
            if citation.key() in seen:
                return
            seen.add(citation.key())
            citations.append(citation)

        for value in MARKER_PATTERN.findall(content):
            clause = allowed.get(int(value))
            if clause is not None:
                add(clause)
        # 答案直接写出条款编号时也建立对应关系（如“依据 4.1.1”）。
        for clause in allowed.values():
            if not clause.clause_no:
                continue
            pattern = re.compile(rf"(?<![\d.]){re.escape(clause.clause_no)}(?![\d.])")
            if pattern.search(content):
                add(clause)
        return citations


def _split_markdown_prefix(line: str) -> tuple[str, str]:
    """拆分列表/引用前缀，正文仍参与引用校验与断言判定。"""
    match = re.match(r"^(\s*(?:[-*+]\s|\d+[.)]\s|>\s?)+)(.*)$", line)
    if match:
        return match.group(1), match.group(2)
    return "", line


_LEADING_MARKERS = re.compile(r"^\s*((?:\[\d{1,2}\]\s*)+)")


def _merge_trailing_markers(parts: list[str]) -> list[str]:
    """把紧随句号之后的 ``[1]`` 合并回前一句，避免把有依据的结论误判为无依据。"""
    merged: list[str] = []
    for part in parts:
        if merged:
            leading = _LEADING_MARKERS.match(part)
            if leading:
                merged[-1] += leading.group(1)
                part = part[leading.end() :]
                if not part.strip():
                    continue
        merged.append(part)
    return merged


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
