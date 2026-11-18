"""向量召回端口与内存实现（P0）。

- ``VectorSearcher`` 是检索服务依赖的后端端口，生产环境可替换为 Milvus 适配；
- ``InMemoryVectorSearcher`` 做**前置过滤**（知识库、租户、密级、标准号、条款号、
  机构、域、实施时间）后再计算余弦相似度，确保越权/密级不符条款在召回阶段就被排除；
- 编号锚点硬过滤：标准号/条款号命中时直接锁定对应条款（编号查询直达），
  条款精确命中固定得分 1.0。
"""

from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Protocol, Sequence

from aipaas_retrieval.models import RetrievalFilters
from aipaas_retrieval.query_understanding import (
    clause_no_matches,
    normalize_clause_no,
    standard_no_matches,
)
from aipaas_retrieval.security import can_read_level


class QueryEncoder(Protocol):
    """查询编码器：必须与入库模型维度一致。"""

    @property
    def dimension(self) -> int: ...

    def encode(self, text: str) -> list[float]: ...


@dataclass(slots=True)
class VectorRecord:
    """入库切片：文本 + 向量 + 金融标准六要素/权限元数据。"""

    chunk_id: str
    document_id: str
    kb_id: str
    text: str
    vector: list[float]
    tenant_id: str = "default"
    clause_no: str | None = None
    clause_title: str | None = None
    chapter_path: str | None = None
    page_no: int | None = None
    standard_no: str | None = None
    standard_name: str | None = None
    issuer: str | None = None
    domain: str | None = None
    effective_at: str | None = None
    security_level: str = "PUBLIC"

    def __post_init__(self) -> None:
        self.chunk_id = str(self.chunk_id)
        self.document_id = str(self.document_id)
        self.kb_id = str(self.kb_id)
        self.tenant_id = str(self.tenant_id or "default")
        self.security_level = str(self.security_level or "PUBLIC").upper()
        self.vector = [float(value) for value in self.vector]


@dataclass(slots=True)
class SearchQuery:
    """一次召回请求：权限前置条件已由服务层收敛为 ``kb_ids``/``max_security_level``。"""

    vector: Sequence[float]
    top_k: int
    kb_ids: tuple[str, ...] | None
    tenant_id: str
    max_security_level: str = "PUBLIC"
    filters: RetrievalFilters = field(default_factory=RetrievalFilters)
    standard_no: str | None = None
    clause_no: str | None = None


@dataclass(slots=True)
class SearchHit:
    record: VectorRecord
    score: float


class VectorSearcher(Protocol):
    def search(self, query: SearchQuery) -> list[SearchHit]: ...


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"向量维度不一致：查询 {len(left)} vs 入库 {len(right)}")
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


def _unit_score(cosine: float) -> float:
    """余弦 [-1,1] → [0,1]，与契约 score 的取值范围一致。"""
    return min(1.0, max(0.0, (cosine + 1.0) / 2.0))


class InMemoryVectorSearcher:
    """内存向量检索：按 chunkId 幂等 upsert，支持前置过滤与编号锚点直达。"""

    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 写入

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        with self._lock:
            for record in records:
                self._records[record.chunk_id] = record
            return len(self._records)

    def delete_kb(self, kb_id: str) -> int:
        with self._lock:
            targets = [key for key, record in self._records.items() if record.kb_id == kb_id]
            for key in targets:
                self._records.pop(key, None)
            return len(targets)

    def count(self, kb_id: str | None = None) -> int:
        with self._lock:
            if kb_id is None:
                return len(self._records)
            return sum(1 for record in self._records.values() if record.kb_id == kb_id)

    def snapshot(self) -> list[VectorRecord]:
        with self._lock:
            return list(self._records.values())

    # ------------------------------------------------------------------ 检索

    def search(self, query: SearchQuery) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for record in self.snapshot():
            if not self._prefilter(record, query):
                continue
            hits.append(SearchHit(record=record, score=self._score(record, query)))
        hits.sort(key=lambda hit: (-hit.score, hit.record.chunk_id))
        return hits[: max(0, query.top_k)]

    def _prefilter(self, record: VectorRecord, query: SearchQuery) -> bool:
        # 1) 知识库/租户：行级权限前置，越权 kb 不进入打分
        if query.kb_ids is not None and record.kb_id not in query.kb_ids:
            return False
        if record.tenant_id and query.tenant_id and record.tenant_id != query.tenant_id:
            return False
        # 2) 密级：高于调用方密级上限的条款不进入候选集
        if not can_read_level(record.security_level, query.max_security_level):
            return False

        filters = query.filters
        if filters.standard_no and not standard_no_matches(record.standard_no, filters.standard_no):
            return False
        if filters.clause_no and not clause_no_matches(record.clause_no, filters.clause_no):
            return False
        if filters.issuer and filters.issuer.casefold() not in str(record.issuer or "").casefold():
            return False
        if filters.domain and filters.domain.casefold() not in str(record.domain or "").casefold():
            return False
        if filters.security_level and not can_read_level(record.security_level, filters.security_level):
            return False

        effective = _as_date(record.effective_at)
        start = _as_date(filters.effective_from)
        end = _as_date(filters.effective_to)
        if start is not None and (effective is None or effective < start):
            return False
        if end is not None and (effective is None or effective > end):
            return False

        # 3) 编号锚点：命中即锁定（编号查询直达）
        if query.standard_no and not standard_no_matches(record.standard_no, query.standard_no):
            return False
        if query.clause_no and not clause_no_matches(record.clause_no, query.clause_no):
            return False
        return True

    def _score(self, record: VectorRecord, query: SearchQuery) -> float:
        if query.clause_no and clause_no_matches(record.clause_no, query.clause_no):
            # 条款编号精确定位：固定最高分，保证直达结果排在首位
            return 1.0
        similarity = _unit_score(_cosine(query.vector, record.vector))
        if query.standard_no and standard_no_matches(record.standard_no, query.standard_no):
            # 标准号命中：在标准范围内按语义排序，同时保证明显高于普通命中
            return 0.5 + 0.5 * similarity
        return similarity


class HashingQueryEncoder:
    """确定性查询编码器：与入库侧 ``SHA256-HASH`` 算法一致，离线可复现。"""

    def __init__(self, dimension: int = 64, model: str = "SHA256-HASH") -> None:
        self._dimension = int(dimension)
        self.model = model
        if self._dimension < 1:
            raise ValueError("dimension 必须为正整数")

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, text: str) -> list[float]:
        digest = hashlib.sha256((text or "").encode("utf-8")).digest()
        raw = (digest * (self._dimension // len(digest) + 1))[: self._dimension]
        vector = [value / 255.0 - 0.5 for value in raw]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def record_from_chunk(chunk: Any, *, kb_id: str, vector: Sequence[float], tenant_id: str = "default") -> VectorRecord:
    """由分块产物构造检索记录（兼容 ``Chunk`` dataclass 与 dict）。"""

    def get(name: str, default: Any = None) -> Any:
        if isinstance(chunk, dict):
            return chunk.get(name, default)
        return getattr(chunk, name, default)

    meta = get("meta") or {}
    return VectorRecord(
        chunk_id=get("chunk_id") or get("chunkId"),
        document_id=get("doc_id") or get("documentId") or get("docId"),
        kb_id=kb_id,
        text=get("content") or get("text") or "",
        vector=list(vector),
        tenant_id=meta.get("tenantId") or tenant_id,
        clause_no=get("clause_no") or get("clauseNo"),
        clause_title=get("clause_title") or get("clauseTitle"),
        chapter_path=get("chapter_path") or get("chapterPath"),
        page_no=meta.get("pageNo"),
        standard_no=meta.get("standardNo"),
        standard_name=meta.get("standardName"),
        issuer=meta.get("issuer"),
        domain=meta.get("domain"),
        effective_at=meta.get("effectiveAt"),
        security_level=meta.get("securityLevel") or "PUBLIC",
    )


__all__ = [
    "HashingQueryEncoder",
    "InMemoryVectorSearcher",
    "QueryEncoder",
    "SearchHit",
    "SearchQuery",
    "VectorRecord",
    "VectorSearcher",
    "normalize_clause_no",
    "record_from_chunk",
]
