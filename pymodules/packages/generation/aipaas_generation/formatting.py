"""#110 答案后处理：Markdown 格式化与超长答案分段。

约束：只做结构与空白整理，不新增、不删除任何信息（``plain_information`` 前后一致）。
列表、引用、表格原样保留；超长答案按句/段边界切分并用空行连接，保证可连续阅读。
引用以结构化 ``citations`` 返回，默认不注入正文，避免改变答案内容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from aipaas_generation.models import Citation

_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])")
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def plain_information(text: str) -> str:
    """去掉全部空白后的信息签名，用于断言格式化不增删内容。"""
    return re.sub(r"\s+", "", text or "")


@dataclass(slots=True)
class FormattingResult:
    content: str
    segments: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def segment_count(self) -> int:
        return len(self.segments)


class MarkdownFormatter:
    """Markdown 整理器；分段只影响排版，不改变文字信息。"""

    def __init__(self, *, max_segment_chars: int = 1200) -> None:
        if max_segment_chars < 1:
            raise ValueError("max_segment_chars 必须为正整数")
        self.max_segment_chars = max_segment_chars

    def format(
        self,
        content: str,
        *,
        citations: Sequence[Citation] | None = None,
        append_citations: bool = False,
    ) -> FormattingResult:
        normalized = self.normalize(content)
        blocks = [block for block in re.split(r"\n{2,}", normalized) if block.strip("\n")]
        long_answer = any(len(block) > self.max_segment_chars for block in blocks)
        segments = self.segment(normalized)
        warnings: list[str] = []
        if long_answer:
            warnings.append(
                f"ANSWER_SEGMENTED: 答案超过 {self.max_segment_chars} 字符，已切分为 {len(segments)} 段"
            )
        rendered = "\n\n".join(segments)
        if append_citations and citations:
            appendix = render_citations(citations)
            if appendix:
                rendered = f"{rendered}\n\n{appendix}" if rendered else appendix
        return FormattingResult(content=rendered, segments=segments, warnings=warnings)

    @staticmethod
    def normalize(content: str) -> str:
        text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
        text = _TRAILING_SPACES.sub("", text)
        text = _MULTI_NEWLINE.sub("\n\n", text)
        return text.strip()

    def segment(self, content: str) -> list[str]:
        """按空行段落切分；单段超长时优先按句边界，再退化到硬切分。"""
        if not content:
            return []
        segments: list[str] = []
        for block in re.split(r"\n{2,}", content):
            block = block.strip("\n")
            if not block:
                continue
            segments.extend(self._split_block(block))
        return segments

    def _split_block(self, block: str) -> list[str]:
        if len(block) <= self.max_segment_chars:
            return [block]
        # 列表/表格按行保持完整，避免把一行切成两半；其余按句边界聚合。
        units = self._units(block)
        chunks: list[str] = []
        buffer = ""
        for unit in units:
            if len(unit) > self.max_segment_chars:
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                chunks.extend(
                    unit[start : start + self.max_segment_chars]
                    for start in range(0, len(unit), self.max_segment_chars)
                )
                continue
            if buffer and len(buffer) + len(unit) > self.max_segment_chars:
                chunks.append(buffer)
                buffer = unit
            else:
                buffer += unit
        if buffer:
            chunks.append(buffer)
        return chunks

    @staticmethod
    def _units(block: str) -> list[str]:
        if "\n" in block:
            lines = block.split("\n")
            return [line + ("\n" if index < len(lines) - 1 else "") for index, line in enumerate(lines)]
        parts = [part for part in _SENTENCE_BOUNDARY.split(block) if part]
        return parts or [block]


def render_citations(citations: Sequence[Citation]) -> str:
    """可选的引用区块（默认不注入正文）；保持 Markdown 引用 + 列表格式。"""
    if not citations:
        return ""
    lines = ["> **引用条款**"]
    for index, citation in enumerate(citations, start=1):
        lines.append(f"> - [{index}] {citation.label()}")
    return "\n".join(lines)
