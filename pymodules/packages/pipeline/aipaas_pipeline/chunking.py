"""文档分块：#36 固定长度、#37 结构化、#404 条目化。

设计要点：
- 统一 ``Chunk`` 模型（chunkId/docId/clauseNo/clauseTitle/chapterPath/content/tokenCount/hash/meta）；
- 分块参数由 ``ChunkConfig`` 显式声明，``config_version`` 由参数摘要生成，随产物落版本；
- 分块只依赖文档内容与配置，因此同一文档重复分块产生相同的 chunkId 与 hash；
- 结构化/条目化分块的条款 chunk 数必须等于解析出的条款数，否则抛 ``ChunkingInvariantError``。

token 计数默认使用确定性估算（中日韩字符≈1 token，ASCII 单词≈4 字符/token），
可通过 ``tokenizer`` 参数注入 tiktoken 等真实分词器；估算不依赖网络，保证离线可复现。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from aipaas_pipeline.models import Clause, ParsedDocument, SectionNode, TableBlock

CHUNK_RULES_VERSION = "p0-chunk-v1"
STRATEGIES = ("fixed", "structured", "itemized")

TokenCounter = Callable[[str], int]

_CJK_RE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_NON_WORD_RE = re.compile(r"[^A-Za-z0-9\s]")
_SENTENCE_END = "。！？；!?;"
_CLOSERS = "”’」』）)]】》 \t"


def estimate_tokens(text: str) -> int:
    """确定性 token 估算：CJK 字符 1 个、ASCII 单词约 4 字符 1 个、符号 1 个。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = _CJK_RE.sub(" ", text)
    words = sum((len(word) + 3) // 4 for word in _WORD_RE.findall(rest))
    symbols = len(_NON_WORD_RE.findall(rest))
    return cjk + words + symbols


def _char_tokens(char: str) -> float:
    if char.isspace():
        return 0.0
    if _CJK_RE.match(char):
        return 1.0
    if ("a" <= char <= "z") or ("A" <= char <= "Z") or char.isdigit():
        return 0.25
    return 1.0


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class ChunkConfig:
    """分块参数：strategy/size/overlap 可配置，其余为策略行为开关。"""

    strategy: str = "structured"
    size: int = 800
    overlap: int = 100
    sentence_aligned: bool = True
    include_code_blocks: bool = True
    include_tables: bool = True

    def __post_init__(self) -> None:
        self.strategy = str(self.strategy or "structured").strip().lower()
        if self.strategy not in STRATEGIES:
            raise ValueError(f"strategy 仅支持 {STRATEGIES}，收到 {self.strategy!r}")
        self.size = int(self.size)
        self.overlap = int(self.overlap)
        if not 32 <= self.size <= 8000:
            raise ValueError("size 必须在 32..8000 token 之间")
        if not 0 <= self.overlap < self.size:
            raise ValueError("overlap 必须满足 0 ≤ overlap < size")
        self.sentence_aligned = _as_bool(self.sentence_aligned, True)
        self.include_code_blocks = _as_bool(self.include_code_blocks, True)
        self.include_tables = _as_bool(self.include_tables, True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "size": self.size,
            "overlap": self.overlap,
            "sentenceAligned": self.sentence_aligned,
            "includeCodeBlocks": self.include_code_blocks,
            "includeTables": self.include_tables,
        }

    @property
    def config_version(self) -> str:
        digest = hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:8]
        return f"{self.strategy}-{CHUNK_RULES_VERSION}-{digest}"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "ChunkConfig":
        if not values:
            return cls()
        alias = {
            "chunkStrategy": "strategy",
            "chunkSize": "size",
            "chunkOverlap": "overlap",
            "sentenceAligned": "sentence_aligned",
            "includeCodeBlocks": "include_code_blocks",
            "includeTables": "include_tables",
            "strategy": "strategy",
            "size": "size",
            "overlap": "overlap",
            "sentence_aligned": "sentence_aligned",
            "include_code_blocks": "include_code_blocks",
            "include_tables": "include_tables",
        }
        filtered: dict[str, Any] = {}
        for key, value in values.items():
            target = alias.get(key)
            if target is None or value is None:
                continue
            filtered[target] = value
        return cls(**filtered)


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    ordinal: int
    chunk_type: str
    content: str
    clause_no: str | None = None
    clause_title: str = ""
    chapter_path: str = ""
    page_no: int | None = None
    token_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.token_count <= 0:
            self.token_count = estimate_tokens(self.content)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chunkId": self.chunk_id,
            "docId": self.document_id,
            "ordinal": self.ordinal,
            "type": self.chunk_type,
            "clauseNo": self.clause_no,
            "clauseTitle": self.clause_title,
            "chapterPath": self.chapter_path,
            "content": self.content,
            "tokenCount": self.token_count,
            "hash": self.content_hash,
            "meta": self.meta,
        }
        if self.page_no is not None:
            payload["pageNo"] = self.page_no
        return payload


class ChunkingInvariantError(ValueError):
    """分块结果与文档条款数不一致等不变量被破坏。"""


def build_chunks(
    document: ParsedDocument,
    config: ChunkConfig | None = None,
    *,
    tokenizer: TokenCounter | None = None,
) -> list[Chunk]:
    """按配置构建分块；同一文档 + 同一配置的结果完全确定。"""
    config = config or ChunkConfig()
    counter = tokenizer or estimate_tokens
    if config.strategy == "fixed":
        return _fixed_chunks(document, config, counter)
    if config.strategy == "itemized":
        return _itemized_chunks(document, config, counter)
    return _structured_chunks(document, config, counter)


def dedupe_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """按 chunkId + hash 去重，保持首次出现顺序（重复分块的幂等保障）。"""
    seen: set[tuple[str, str]] = set()
    unique: list[Chunk] = []
    for chunk in chunks:
        key = (chunk.chunk_id, chunk.content_hash)
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


# --------------------------------------------------------------------------- #37 结构化


def _structured_chunks(document: ParsedDocument, config: ChunkConfig, counter: TokenCounter) -> list[Chunk]:
    chunks: list[Chunk] = []
    if document.clauses:
        for index, clause in enumerate(document.clauses):
            content = _clause_content(clause, with_title=True)
            if not content:
                content = clause.title or clause.clause_no
            chunks.append(
                Chunk(
                    chunk_id=f"clause:{index}:{clause.clause_no or clause.section_id or 'ann'}",
                    document_id=document.doc_id,
                    ordinal=len(chunks),
                    chunk_type="clause",
                    content=content,
                    clause_no=clause.clause_no or None,
                    clause_title=clause.title,
                    chapter_path=clause.chapter_path,
                    page_no=clause.page_no,
                    token_count=counter(content),
                    meta={
                        "strategy": config.strategy,
                        "configVersion": config.config_version,
                        "sectionId": clause.section_id,
                        "level": clause.level,
                        "partIndex": 0,
                        "partCount": 1,
                    },
                )
            )
    elif document.raw_text.strip():
        content = document.raw_text.strip()
        chunks.append(
            Chunk(
                chunk_id="document:0",
                document_id=document.doc_id,
                ordinal=len(chunks),
                chunk_type="document",
                content=content,
                chapter_path=_first_section_title(document.chapter_tree),
                token_count=counter(content),
                meta={"strategy": config.strategy, "configVersion": config.config_version},
            )
        )

    if config.include_code_blocks:
        for index, block in enumerate(document.extras.get("codeBlocks", []) or []):
            content = str(block.get("content", ""))
            if not content:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"code:{index}",
                    document_id=document.doc_id,
                    ordinal=len(chunks),
                    chunk_type="code",
                    content=content,
                    token_count=counter(content),
                    meta={
                        "strategy": config.strategy,
                        "configVersion": config.config_version,
                        "language": block.get("language", ""),
                        "startLine": block.get("startLine"),
                        "endLine": block.get("endLine"),
                    },
                )
            )

    if config.include_tables:
        for index, table in enumerate(document.tables):
            content = _table_content(table)
            if not content:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"table:{index}:{table.table_id}",
                    document_id=document.doc_id,
                    ordinal=len(chunks),
                    chunk_type="table",
                    content=content,
                    chapter_path=table.caption or "",
                    page_no=table.page_no,
                    token_count=counter(content),
                    meta={
                        "strategy": config.strategy,
                        "configVersion": config.config_version,
                        "tableId": table.table_id,
                        "rowCount": len(table.rows),
                        "headerRows": table.header_rows,
                    },
                )
            )
    return chunks


# --------------------------------------------------------------------------- #404 条目化


def _itemized_chunks(document: ParsedDocument, config: ChunkConfig, counter: TokenCounter) -> list[Chunk]:
    """一条款一 chunk：编号 + 正文；条目数严格等于条款数。"""
    chunks: list[Chunk] = []
    for index, clause in enumerate(document.clauses):
        content = _clause_content(clause, with_title=True)
        chunks.append(
            Chunk(
                chunk_id=f"item:{index}:{clause.clause_no or clause.section_id or 'ann'}",
                document_id=document.doc_id,
                ordinal=index,
                chunk_type="clause",
                content=content,
                clause_no=clause.clause_no or None,
                clause_title=clause.title,
                chapter_path=clause.chapter_path,
                page_no=clause.page_no,
                token_count=counter(content),
                meta={
                    "strategy": config.strategy,
                    "configVersion": config.config_version,
                    "anchor": "clause_no",
                    "sectionId": clause.section_id,
                    "level": clause.level,
                    "partCount": 1,
                },
            )
        )
    return chunks


# --------------------------------------------------------------------------- #36 固定长度


def _fixed_chunks(document: ParsedDocument, config: ChunkConfig, counter: TokenCounter) -> list[Chunk]:
    segments = _segments(document)
    text = "\n\n".join(segment["text"] for segment in segments)
    if not text.strip():
        return []

    offsets: list[tuple[int, int, dict[str, Any]]] = []
    cursor = 0
    for segment in segments:
        start = cursor
        end = start + len(segment["text"])
        offsets.append((start, end, segment))
        cursor = end + 2  # "\n\n"

    units = _units(text, config.sentence_aligned, config.size, counter)
    windows = _pack_units(text, units, config, counter)
    chunks: list[Chunk] = []
    previous_window: tuple[int, int, int] | None = None
    for start_index, end_index, _tokens in windows:
        start_offset = units[start_index][0]
        end_offset = units[end_index - 1][1]
        content = text[start_offset:end_offset].strip()
        if not content:
            previous_window = (start_index, end_index, _tokens)
            continue
        clause_numbers: list[str] = []
        for seg_start, seg_end, segment in offsets:
            if seg_start < end_offset and seg_end > start_offset:
                clause_no = segment.get("clause_no")
                if clause_no and clause_no not in clause_numbers:
                    clause_numbers.append(clause_no)
        anchor = _segment_at(offsets, start_offset)
        clause_no = anchor.get("clause_no") if anchor else None
        overlap = 0
        if previous_window is not None:
            _, previous_end, _ = previous_window
            if start_index < previous_end:
                overlap = sum(
                    counter(text[units[index][0]:units[index][1]])
                    for index in range(start_index, previous_end)
                )
        chunks.append(
            Chunk(
                chunk_id=f"window:{len(chunks)}:{hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]}",
                document_id=document.doc_id,
                ordinal=len(chunks),
                chunk_type="window",
                content=content,
                clause_no=clause_no,
                clause_title=anchor.get("clause_title", "") if anchor else "",
                chapter_path=anchor.get("chapter_path", "") if anchor else "",
                page_no=anchor.get("page_no") if anchor else None,
                token_count=counter(content),
                meta={
                    "strategy": config.strategy,
                    "configVersion": config.config_version,
                    "startOffset": start_offset,
                    "endOffset": end_offset,
                    "overlapTokens": overlap,
                    "sentenceAligned": config.sentence_aligned,
                    "clauseNos": clause_numbers,
                    "preview": content[:80],
                },
            )
        )
        previous_window = (start_index, end_index, _tokens)
    return chunks


def _segments(document: ParsedDocument) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if document.clauses:
        for clause in document.clauses:
            header = " ".join(part for part in (clause.clause_no, clause.title) if part).strip()
            text = f"{header}\n{clause.text}".strip() if clause.text else header
            if not text:
                continue
            segments.append(
                {
                    "text": text,
                    "clause_no": clause.clause_no or None,
                    "clause_title": clause.title,
                    "chapter_path": clause.chapter_path,
                    "page_no": clause.page_no,
                }
            )
        for block in document.extras.get("codeBlocks", []) or []:
            content = str(block.get("content", "")).strip()
            if content:
                segments.append({"text": content, "clause_no": None, "clause_title": "",
                                 "chapter_path": "", "page_no": None})
        for table in document.tables:
            content = _table_content(table)
            if content:
                segments.append({"text": content, "clause_no": None, "clause_title": "",
                                 "chapter_path": table.caption or "", "page_no": table.page_no})
    elif document.raw_text.strip():
        segments.append({"text": document.raw_text.strip(), "clause_no": None, "clause_title": "",
                         "chapter_path": _first_section_title(document.chapter_tree), "page_no": None})
    return segments


def _segment_at(offsets: list[tuple[int, int, dict[str, Any]]], position: int) -> dict[str, Any] | None:
    for start, end, segment in offsets:
        if start <= position <= end:
            return segment
    return offsets[-1][2] if offsets else None


def _units(text: str, sentence_aligned: bool, size: int, counter: TokenCounter) -> list[tuple[int, int]]:
    raw_units = _sentence_units(text) if sentence_aligned else _token_units(text)
    return _cap_units(text, raw_units, size, counter)


def _sentence_units(text: str) -> list[tuple[int, int]]:
    units: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in _SENTENCE_END or char == "\n":
            end = index + 1
            while end < len(text) and text[end] in _CLOSERS:
                end += 1
            if text[start:end].strip():
                units.append((start, end))
            start = end
            index = end
            continue
        index += 1
    if start < len(text) and text[start:].strip():
        units.append((start, len(text)))
    return units


def _token_units(text: str) -> list[tuple[int, int]]:
    units: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(text):
        if char.isspace():
            if start is not None:
                units.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        units.append((start, len(text)))
    return units


def _cap_units(
    text: str, units: list[tuple[int, int]], size: int, counter: TokenCounter
) -> list[tuple[int, int]]:
    capped: list[tuple[int, int]] = []
    for start, end in units:
        if counter(text[start:end]) <= size:
            capped.append((start, end))
            continue
        segment_start = start
        tokens = 0.0
        for index in range(start, end):
            cost = _char_tokens(text[index])
            if tokens + cost > size and index > segment_start:
                capped.append((segment_start, index))
                segment_start = index
                tokens = 0.0
            tokens += cost
        if segment_start < end:
            capped.append((segment_start, end))
    return capped


def _pack_units(
    text: str, units: list[tuple[int, int]], config: ChunkConfig, counter: TokenCounter
) -> list[tuple[int, int, int]]:
    unit_tokens = [counter(text[start:end]) for start, end in units]
    windows: list[tuple[int, int, int]] = []
    index = 0
    while index < len(units):
        start_index = index
        total = 0
        end_index = index
        while end_index < len(units):
            tokens = unit_tokens[end_index]
            if end_index > start_index and total + tokens > config.size:
                break
            total += tokens
            end_index += 1
        windows.append((start_index, end_index, total))
        if end_index >= len(units):
            break
        if config.overlap <= 0:
            index = end_index
            continue
        overlap = 0
        cursor = end_index - 1
        while cursor > start_index and overlap + unit_tokens[cursor] <= config.overlap:
            overlap += unit_tokens[cursor]
            cursor -= 1
        if cursor == end_index - 1:
            # 句粒度重叠：单句长于 overlap 时至少重叠 1 句，避免窗口之间完全断开
            cursor = end_index - 2
        index = max(cursor + 1, start_index + 1)
    return windows


def _clause_content(clause: Clause, *, with_title: bool) -> str:
    header = " ".join(part for part in (clause.clause_no, clause.title if with_title else "") if part).strip()
    body = clause.text.strip()
    if header and body:
        return f"{header}\n{body}" if not body.startswith(header) else body
    return header or body


def _table_content(table: TableBlock) -> str:
    return "\n".join("\t".join(cell for cell in row) for row in table.rows if row)


def _first_section_title(nodes: list[SectionNode]) -> str:
    return nodes[0].title if nodes else ""


# --------------------------------------------------------------------------- 不变量


def chunk_invariants(document: ParsedDocument, chunks: list[Chunk], config: ChunkConfig) -> dict[str, Any]:
    clause_chunks = [chunk for chunk in chunks if chunk.chunk_type == "clause"]
    if config.strategy == "itemized":
        matches = len(chunks) == len(document.clauses)
    elif config.strategy == "structured":
        matches = len(clause_chunks) == len(document.clauses)
    else:
        matches = True
    clause_numbers = [chunk.clause_no for chunk in clause_chunks]
    return {
        "strategy": config.strategy,
        "configVersion": config.config_version,
        "clauseCount": len(document.clauses),
        "clauseChunks": len(clause_chunks),
        "totalChunks": len(chunks),
        "countMatchesClauses": matches,
        "uniqueChunkIds": len({chunk.chunk_id for chunk in chunks}) == len(chunks),
        "uniqueClauseNumbers": len({number for number in clause_numbers if number}) == len(
            [number for number in clause_numbers if number]
        ),
        "avgTokens": round(sum(chunk.token_count for chunk in chunks) / len(chunks), 1) if chunks else 0,
    }


def assert_chunk_invariants(
    document: ParsedDocument, chunks: list[Chunk], config: ChunkConfig
) -> dict[str, Any]:
    result = chunk_invariants(document, chunks, config)
    if not result["countMatchesClauses"]:
        raise ChunkingInvariantError(
            f"{config.strategy} 分块条目数 {result['totalChunks']}（条款 chunk {result['clauseChunks']}）"
            f"与文档条款数 {result['clauseCount']} 不一致"
        )
    if not result["uniqueChunkIds"]:
        raise ChunkingInvariantError("分块存在重复 chunkId")
    return result


# --------------------------------------------------------------------------- 存储


class ChunkStore(Protocol):
    def replace_document(self, document_id: str, chunks: list[Chunk]) -> int: ...

    def count(self, document_id: str) -> int: ...

    def list_chunks(self, document_id: str) -> list[dict[str, object]]: ...


class InMemoryChunkStore:
    """测试/单进程实现；按 chunkId 覆盖，重复写入幂等。"""

    def __init__(self) -> None:
        self._documents: dict[str, dict[str, dict[str, object]]] = {}
        self._lock = threading.RLock()

    def replace_document(self, document_id: str, chunks: list[Chunk]) -> int:
        with self._lock:
            self._documents[document_id] = _index_chunks(chunks)
            return len(self._documents[document_id])

    def count(self, document_id: str) -> int:
        with self._lock:
            return len(self._documents.get(document_id, {}))

    def list_chunks(self, document_id: str) -> list[dict[str, object]]:
        with self._lock:
            return list(self._documents.get(document_id, {}).values())


class FileChunkStore:
    """文件实现：``<root>/<document_id>/current.json`` 原子覆盖。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()

    def replace_document(self, document_id: str, chunks: list[Chunk]) -> int:
        with self._lock:
            directory = self.root / _safe(document_id)
            directory.mkdir(parents=True, exist_ok=True)
            payload = list(_index_chunks(chunks).values())
            target = directory / "current.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, target)
            return len(payload)

    def count(self, document_id: str) -> int:
        return len(self._read(document_id))

    def list_chunks(self, document_id: str) -> list[dict[str, object]]:
        return self._read(document_id)

    def _read(self, document_id: str) -> list[dict[str, object]]:
        target = self.root / _safe(document_id) / "current.json"
        if not target.exists():
            return []
        return json.loads(target.read_text(encoding="utf-8"))


def _index_chunks(chunks: list[Chunk]) -> dict[str, dict[str, object]]:
    return {chunk.chunk_id: chunk.to_dict() for chunk in dedupe_chunks(chunks)}


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:128]
