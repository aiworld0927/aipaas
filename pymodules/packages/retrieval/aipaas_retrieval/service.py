"""#70 向量语义召回 + #88 行级权限过滤的编排服务。

流程（耗时全部计入 ``latencyMs``，满足检索 + 重排 P95 ≤ 500ms 的口径）：

1. 查询归一（#63）→ 2. 权限校验（显式越权 KB 直接拒绝并审计）→ 3. 查询编码 →
4. 单路向量召回（带超时，超时降级为 ``degraded=true``）→ 5. 召回结果密级/知识库复核兜底 →
6. 审计 ``retrieval.query``。

P0 仅执行 ``retrievalMode=vector`` 且不重排；其它模式返回明确的“未启用”告警而非报错。
"""

from __future__ import annotations

import functools
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Sequence

from aipaas_common.core.observability import mark_retrieval, span

from aipaas_retrieval.audit import (
    ACTION_DENIED,
    ACTION_QUERY,
    AuditSink,
    audit_entry,
    safe_record,
)
from aipaas_retrieval.errors import PermissionDenied
from aipaas_retrieval.models import (
    RETRIEVAL_MODE_VECTOR,
    RetrieveHit,
    RetrieveRequest,
    RetrieveResult,
    RetrievalFilters,
)
from aipaas_retrieval.query_understanding import (
    MAX_QUERY_CHARS,
    normalize_clause_no,
    normalize_standard_no,
    understand_query,
)
from aipaas_retrieval.security import (
    PERMISSION_RETRIEVAL_QUERY,
    AccessContext,
)
from aipaas_retrieval.store import QueryEncoder, SearchHit, SearchQuery, VectorSearcher

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievalConfig:
    """检索参数：topK 上限与超时预算；P95 目标 500ms，超时预算留足余量。"""

    max_top_k: int = 50
    max_kb_ids: int = 20
    max_query_chars: int = MAX_QUERY_CHARS
    timeout_ms: int = 300

    def __post_init__(self) -> None:
        if self.timeout_ms < 1:
            raise ValueError("timeout_ms 必须为正整数")
        if self.max_top_k < 1:
            raise ValueError("max_top_k 必须为正整数")


def _observed_retrieve(method):
    """为检索入口补充 Span 与命中率指标；异常仍按原语义向上抛出。"""

    @functools.wraps(method)
    def wrapper(self, request: RetrieveRequest, access: AccessContext) -> RetrieveResult:
        started = time.perf_counter()
        result: RetrieveResult | None = None
        with span("retrieve.query", tenantId=access.tenant_id, topK=request.top_k,
                  retrievalMode=request.retrieval_mode):
            try:
                result = method(self, request, access)
                return result
            finally:
                mark_retrieval(
                    bool(result and result.hits),
                    time.perf_counter() - started,
                    service="app-query-worker",
                )

    return wrapper


class RetrievalService:
    """检索编排：召回阶段完成权限过滤，生成阶段拿不到任何越权条款。"""

    def __init__(
        self,
        searcher: VectorSearcher,
        encoder: QueryEncoder,
        *,
        audit: AuditSink | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.searcher = searcher
        self.encoder = encoder
        self.audit = audit
        self.config = config or RetrievalConfig()
        self._pool: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------------ 对外入口

    @_observed_retrieve
    def retrieve(self, request: RetrieveRequest, access: AccessContext) -> RetrieveResult:
        started = time.perf_counter()
        request.validate(max_top_k=self.config.max_top_k, max_kb_ids=self.config.max_kb_ids)

        understanding = understand_query(request.query, max_length=self.config.max_query_chars)
        timings: dict[str, float] = {"queryUnderstandingMs": understanding.latency_ms}
        warnings = list(understanding.warnings)
        mode = request.retrieval_mode

        # 1) 权限：缺失检索权限直接拒绝（不进入召回）
        if not access.has_permission(PERMISSION_RETRIEVAL_QUERY):
            self._deny(request, access, "PERMISSION_MISSING", None)
            raise PermissionDenied("缺少检索权限 retrieval:query", reason="PERMISSION_MISSING")

        # 2) 知识库范围：显式请求越权库 → 拒绝 + 审计；未指定 → 取有权限集合
        requested = request.requested_kb_ids()
        if requested is not None:
            forbidden = [kb for kb in requested if not access.can_query_kb(kb)]
            if forbidden:
                self._deny(request, access, "KB_FORBIDDEN", forbidden[0])
                raise PermissionDenied(
                    f"无权检索知识库：{forbidden[0]}", reason="KB_FORBIDDEN", kb_id=forbidden[0]
                )
            kb_ids: tuple[str, ...] | None = tuple(requested)
        else:
            kb_ids = access.authorized_kb_ids()
            if kb_ids is not None and not kb_ids:
                warnings.append("NO_AUTHORIZED_KB: 当前用户没有可检索的知识库")
                return self._finish_empty(
                    request, understanding, warnings, timings, started, access, "NO_AUTHORIZED_KB"
                )

        # 3) 未启用的能力：明确返回“未启用”，不报错
        if mode != RETRIEVAL_MODE_VECTOR:
            warnings.append(
                f"RETRIEVAL_MODE_NOT_ENABLED: retrievalMode={mode} 未启用"
                "（P1 #71 BM25 / #72 混合召回），P0 仅支持 vector"
            )
            return self._finish_empty(
                request, understanding, warnings, timings, started, access, "MODE_NOT_ENABLED", kb_ids
            )
        if request.rerank:
            warnings.append("RERANK_NOT_ENABLED: 重排未启用（P1），已返回向量召回结果")

        # 4) 空查询/无有效内容兜底
        if understanding.is_blank or (not understanding.tokens and not understanding.has_anchor):
            return self._finish_empty(
                request, understanding, warnings, timings, started, access, "QUERY_EMPTY", kb_ids
            )

        # 5) 查询向量化
        encode_started = time.perf_counter()
        with span("retrieve.embed", tenantId=access.tenant_id) as embed_span:
            try:
                vector = self.encoder.encode(understanding.normalized_query)
            except Exception as exc:  # pragma: no cover - 编码器异常按降级处理
                embed_span.status = "ERROR"
                timings["embeddingMs"] = (time.perf_counter() - encode_started) * 1000
                warnings.append(f"EMBEDDING_FAILED: 查询向量化失败（{type(exc).__name__}），已降级")
                return self._finish_empty(
                    request,
                    understanding,
                    warnings,
                    timings,
                    started,
                    access,
                    "EMBEDDING_FAILED",
                    kb_ids,
                    degraded=True,
                )
        timings["embeddingMs"] = (time.perf_counter() - encode_started) * 1000
        elapsed_ms = (time.perf_counter() - started) * 1000
        remaining = max(0.001, (self.config.timeout_ms - elapsed_ms) / 1000)

        # 6) 单路向量召回（超时降级）
        standard_no = normalize_standard_no(request.filters.standard_no) or understanding.standard_no
        clause_no = normalize_clause_no(request.filters.clause_no) or understanding.clause_no
        search_filters = RetrievalFilters(
            issuer=request.filters.issuer,
            domain=request.filters.domain,
            security_level=request.filters.security_level,
            effective_from=request.filters.effective_from,
            effective_to=request.filters.effective_to,
        )
        search_query = SearchQuery(
            vector=vector,
            top_k=request.top_k,
            kb_ids=kb_ids,
            tenant_id=access.tenant_id,
            max_security_level=access.max_security_level,
            filters=search_filters,
            standard_no=standard_no,
            clause_no=clause_no,
        )
        search_started = time.perf_counter()
        degraded = False
        degraded_reason: str | None = None
        with span("retrieve.search", tenantId=access.tenant_id, topK=request.top_k) as search_span:
            try:
                raw_hits = self._executor().submit(self.searcher.search, search_query).result(timeout=remaining)
            except FutureTimeoutError:
                search_span.status = "ERROR"
                raw_hits = []
                degraded = True
                degraded_reason = "VECTOR_TIMEOUT"
                warnings.append(
                    f"VECTOR_TIMEOUT_DEGRADED: 向量召回超过 {self.config.timeout_ms}ms，已降级返回空结果"
                )
            except Exception as exc:
                search_span.status = "ERROR"
                raw_hits = []
                degraded = True
                degraded_reason = "VECTOR_BACKEND_ERROR"
                logger.warning("向量召回失败，已降级", exc_info=True)
                warnings.append(f"VECTOR_BACKEND_DEGRADED: 向量召回失败（{type(exc).__name__}），已降级")
        timings["searchMs"] = (time.perf_counter() - search_started) * 1000

        # 7) 复核兜底：即使后端误返回，也不允许越权/密级不符条款流出
        guard_started = time.perf_counter()
        hits = self._guard(raw_hits, access, request)
        timings["guardMs"] = (time.perf_counter() - guard_started) * 1000

        latency_ms = int(round((time.perf_counter() - started) * 1000))
        timings["totalMs"] = latency_ms
        result = RetrieveResult(
            query=request.query,
            normalized_query=understanding.normalized_query,
            retrieval_mode=mode,
            rerank=bool(request.rerank),
            hits=hits,
            latency_ms=latency_ms,
            degraded=degraded,
            degraded_reason=degraded_reason,
            warnings=warnings,
            timings=timings,
            trace_id=request.trace_id,
        )
        self._audit_query(request, access, result, kb_ids, standard_no, clause_no)
        return result

    # ------------------------------------------------------------------ 内部

    def _executor(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retrieval")
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def _guard(
        self,
        raw_hits: Sequence[SearchHit],
        access: AccessContext,
        request: RetrieveRequest,
    ) -> list[RetrieveHit]:
        guarded: list[RetrieveHit] = []
        for raw in raw_hits:
            record = raw.record
            denied_reason = None
            if not access.can_query_kb(record.kb_id):
                denied_reason = "KB_FORBIDDEN"
            elif not access.can_read_level(record.security_level):
                denied_reason = "SECURITY_LEVEL"
            elif record.tenant_id and access.tenant_id and record.tenant_id != access.tenant_id:
                denied_reason = "TENANT_MISMATCH"
            if denied_reason:
                safe_record(
                    self.audit,
                    audit_entry(
                        ACTION_DENIED,
                        user_id=access.user_id,
                        tenant_id=access.tenant_id,
                        allowed=False,
                        query=request.query,
                        kb_id=record.kb_id,
                        reason="ROW_FILTERED",
                        trace_id=request.trace_id,
                        detail={"chunkId": record.chunk_id, "documentId": record.document_id,
                                "filter": denied_reason},
                    ),
                )
                continue
            guarded.append(
                RetrieveHit(
                    rank=len(guarded) + 1,
                    score=min(1.0, max(0.0, float(raw.score))),
                    chunk_id=record.chunk_id,
                    document_id=record.document_id,
                    kb_id=record.kb_id,
                    text=record.text,
                    clause_no=record.clause_no,
                    chapter_path=record.chapter_path,
                    page_no=record.page_no,
                    standard_no=record.standard_no,
                    standard_name=record.standard_name,
                    security_level=record.security_level,
                )
            )
        return guarded

    def _deny(
        self,
        request: RetrieveRequest,
        access: AccessContext,
        reason: str,
        kb_id: str | None,
    ) -> None:
        safe_record(
            self.audit,
            audit_entry(
                ACTION_DENIED,
                user_id=access.user_id,
                tenant_id=access.tenant_id,
                allowed=False,
                query=request.query,
                kb_id=kb_id,
                reason=reason,
                trace_id=request.trace_id,
                detail={"retrievalMode": request.retrieval_mode},
            ),
        )

    def _audit_query(
        self,
        request: RetrieveRequest,
        access: AccessContext,
        result: RetrieveResult,
        kb_ids: tuple[str, ...] | None,
        standard_no: str | None,
        clause_no: str | None,
    ) -> None:
        safe_record(
            self.audit,
            audit_entry(
                ACTION_QUERY,
                user_id=access.user_id,
                tenant_id=access.tenant_id,
                allowed=True,
                query=request.query,
                reason=None,
                trace_id=request.trace_id,
                detail={
                    "retrievalMode": result.retrieval_mode,
                    "topK": request.top_k,
                    "kbIds": list(kb_ids) if kb_ids is not None else None,
                    "standardNo": standard_no,
                    "clauseNo": clause_no,
                    "hitCount": len(result.hits),
                    "latencyMs": result.latency_ms,
                    "degraded": result.degraded,
                },
            ),
        )

    def _finish_empty(
        self,
        request: RetrieveRequest,
        understanding,
        warnings: list[str],
        timings: dict[str, float],
        started: float,
        access: AccessContext,
        reason: str,
        kb_ids: tuple[str, ...] | None = None,
        *,
        degraded: bool = False,
    ) -> RetrieveResult:
        latency_ms = int(round((time.perf_counter() - started) * 1000))
        timings["totalMs"] = latency_ms
        result = RetrieveResult(
            query=request.query,
            normalized_query=understanding.normalized_query,
            retrieval_mode=request.retrieval_mode,
            rerank=bool(request.rerank),
            hits=[],
            latency_ms=latency_ms,
            degraded=degraded,
            degraded_reason=reason if degraded else None,
            warnings=warnings,
            timings=timings,
            trace_id=request.trace_id,
        )
        safe_record(
            self.audit,
            audit_entry(
                ACTION_QUERY,
                user_id=access.user_id,
                tenant_id=access.tenant_id,
                allowed=True,
                query=request.query,
                reason=reason,
                trace_id=request.trace_id,
                detail={
                    "retrievalMode": request.retrieval_mode,
                    "topK": request.top_k,
                    "kbIds": list(kb_ids) if kb_ids is not None else None,
                    "hitCount": 0,
                    "latencyMs": latency_ms,
                    "degraded": degraded,
                },
            ),
        )
        return result


__all__ = ["RetrievalConfig", "RetrievalService"]
