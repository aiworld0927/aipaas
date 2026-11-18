"""检索结果模型：请求、过滤条件、命中片段与响应。

字段命名与 ``aipaas-openapi-v1.yaml`` 的 ``RetrieveRequest``/``RetrieveHit``/
``RetrieveResult`` 对齐，``to_dict()`` 输出 camelCase，供 FastAPI 直接返回。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from aipaas_retrieval.errors import InvalidRequest

RETRIEVAL_MODE_VECTOR = "vector"
RETRIEVAL_MODES = ("vector", "bm25", "hybrid")


def _iso(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


@dataclass(slots=True)
class RetrievalFilters:
    """元数据过滤：标准号/机构/域/时间由 P0 生效；密级为兜底限制（行级校验始终生效）。"""

    standard_no: str | None = None
    issuer: str | None = None
    clause_no: str | None = None
    domain: str | None = None
    security_level: str | None = None
    effective_from: str | date | datetime | None = None
    effective_to: str | date | datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "standardNo": self.standard_no,
            "issuer": self.issuer,
            "clauseNo": self.clause_no,
            "domain": self.domain,
            "securityLevel": self.security_level,
            "effectiveFrom": _iso(self.effective_from),
            "effectiveTo": _iso(self.effective_to),
        }


@dataclass(slots=True)
class RetrieveRequest:
    """检索入参；``kbId`` 与 ``kbIds`` 互斥且默认空（空 = 全部有权限的知识库）。"""

    query: str
    kb_id: str | None = None
    kb_ids: list[str] = field(default_factory=list)
    top_k: int = 5
    retrieval_mode: str = RETRIEVAL_MODE_VECTOR
    rerank: bool = False
    session_id: str | None = None
    filters: RetrievalFilters = field(default_factory=RetrievalFilters)
    trace_id: str | None = None

    def validate(self, *, max_top_k: int = 50, max_kb_ids: int = 20) -> None:
        if self.kb_id and self.kb_ids:
            raise InvalidRequest("kbId 与 kbIds 互斥，不能同时传入")
        if len(self.kb_ids) > max_kb_ids:
            raise InvalidRequest(f"kbIds 最多 {max_kb_ids} 个")
        if not 1 <= int(self.top_k) <= max_top_k:
            raise InvalidRequest(f"topK 必须在 1..{max_top_k} 之间")
        self.retrieval_mode = str(self.retrieval_mode or RETRIEVAL_MODE_VECTOR).strip().lower()
        if self.retrieval_mode not in RETRIEVAL_MODES:
            raise InvalidRequest(f"retrievalMode 仅支持 {RETRIEVAL_MODES}")
        self.top_k = int(self.top_k)

    def requested_kb_ids(self) -> list[str] | None:
        """返回显式请求的知识库列表；未指定时返回 ``None``（表示全部有权限的库）。"""
        if self.kb_id:
            return [self.kb_id.strip()] if self.kb_id.strip() else None
        ids = [str(item).strip() for item in self.kb_ids if str(item).strip()]
        return sorted(set(ids)) or None


@dataclass(slots=True)
class RetrieveHit:
    """命中片段：携带引用展示所需的来源文档、条款编号与章节路径。"""

    rank: int
    score: float
    chunk_id: str
    document_id: str
    kb_id: str
    text: str
    clause_no: str | None = None
    chapter_path: str | None = None
    page_no: int | None = None
    standard_no: str | None = None
    standard_name: str | None = None
    security_level: str = "PUBLIC"

    def citation(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            "standardNo": self.standard_no,
            "standardName": self.standard_name,
            "clauseNo": self.clause_no,
            "pageNo": self.page_no,
            "chapterPath": self.chapter_path,
            "quote": None,
            "score": round(self.score, 6),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "score": round(self.score, 6),
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            "kbId": self.kb_id,
            "clauseNo": self.clause_no,
            "chapterPath": self.chapter_path,
            "pageNo": self.page_no,
            "text": self.text,
            "standardNo": self.standard_no,
            "standardName": self.standard_name,
            "securityLevel": self.security_level,
            "citation": self.citation(),
        }


@dataclass(slots=True)
class RetrieveResult:
    """检索结果；``latency_ms`` 覆盖查询处理 + 向量检索 + 权限兜底全链路。"""

    query: str
    normalized_query: str
    retrieval_mode: str
    rerank: bool
    hits: list[RetrieveHit] = field(default_factory=list)
    latency_ms: int = 0
    degraded: bool = False
    degraded_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "normalizedQuery": self.normalized_query,
            "retrievalMode": self.retrieval_mode,
            "rerank": self.rerank,
            "hits": [hit.to_dict() for hit in self.hits],
            "latencyMs": self.latency_ms,
            "degraded": self.degraded,
            "degradedReason": self.degraded_reason,
            "warnings": list(self.warnings),
            "timings": {key: round(value, 3) for key, value in self.timings.items()},
            "traceId": self.trace_id,
        }
