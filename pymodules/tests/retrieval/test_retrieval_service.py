"""#70 向量召回：top-k、元数据过滤、编号直达、未启用能力与超时降级。"""

from __future__ import annotations

import time

import pytest

from aipaas_retrieval import (
    HashingQueryEncoder,
    InMemoryAuditSink,
    InMemoryVectorSearcher,
    InvalidRequest,
    RetrievalConfig,
    RetrievalFilters,
    RetrievalService,
    RetrieveRequest,
    SearchQuery,
    VectorRecord,
    record_from_chunk,
)


def _query(service: RetrievalService, query: str, ctx, **kwargs):
    return service.retrieve(RetrieveRequest(query=query, **kwargs), ctx)


def test_standard_no_anchor_only_returns_that_standard(service: RetrievalService, make_access) -> None:
    result = _query(service, "JR/T 0088", make_access())
    assert result.hits
    assert {hit.standard_no for hit in result.hits} == {"JR/T 0088-2016"}
    assert {hit.chunk_id for hit in result.hits} <= {"a-4.1.1", "a-4.1.2", "a-5.1"}
    assert all(hit.score >= 0.5 for hit in result.hits)


def test_clause_no_anchor_is_direct_hit(service: RetrievalService, make_access) -> None:
    result = _query(service, "第4.1.1条", make_access())
    assert [hit.chunk_id for hit in result.hits] == ["a-4.1.1"]
    assert result.hits[0].score == 1.0
    assert result.hits[0].clause_no == "4.1.1"
    assert result.hits[0].chapter_path.startswith("第四章")


def test_top_k_semantic_relevance(service: RetrievalService, make_access) -> None:
    result = _query(service, "风险评估要求", make_access(), top_k=2)
    assert result.hits[0].chunk_id == "a-4.1.1"
    assert len(result.hits) <= 2
    assert [hit.rank for hit in result.hits] == list(range(1, len(result.hits) + 1))


def test_filters_by_issuer_domain_and_effective_time(service: RetrievalService, make_access) -> None:
    ctx = make_access()
    by_issuer = _query(service, "数据安全", ctx, filters=RetrievalFilters(issuer="国家市场监督管理总局"))
    assert [hit.chunk_id for hit in by_issuer.hits] == ["c-3.2"]

    by_domain = _query(service, "尽职调查", ctx, filters=RetrievalFilters(domain="反洗钱"))
    assert [hit.chunk_id for hit in by_domain.hits] == ["b-5.1"]

    effective_from = _query(service, "数据", ctx, filters=RetrievalFilters(effective_from="2021-01-01"))
    assert {hit.chunk_id for hit in effective_from.hits} == {"c-3.2"}

    effective_to = _query(service, "数据", ctx, filters=RetrievalFilters(effective_to="2018-01-01"))
    assert {hit.chunk_id for hit in effective_to.hits} == {"a-4.1.1", "a-4.1.2", "a-5.1"}


def test_multiple_kb_ids_scope(service: RetrievalService, make_access) -> None:
    result = _query(service, "数据安全", make_access(), kb_ids=["kb_a", "kb_c"])
    assert {hit.kb_id for hit in result.hits} == {"kb_a", "kb_c"}


def test_default_scope_only_authorized_kbs(service: RetrievalService, make_access) -> None:
    result = _query(service, "反洗钱 客户尽职调查", make_access(kb_ids=("kb_a",)))
    assert all(hit.kb_id == "kb_a" for hit in result.hits)
    assert not any(item.startswith("NO_AUTHORIZED_KB") for item in result.warnings)


def test_invalid_requests(service: RetrievalService, make_access) -> None:
    ctx = make_access()
    with pytest.raises(InvalidRequest):
        _query(service, "风险", ctx, kb_id="kb_a", kb_ids=["kb_b"])
    with pytest.raises(InvalidRequest):
        _query(service, "风险", ctx, top_k=0)
    with pytest.raises(InvalidRequest):
        _query(service, "风险", ctx, top_k=51)
    with pytest.raises(InvalidRequest):
        _query(service, "风险", ctx, retrieval_mode="graph")


def test_p1_modes_return_not_enabled_instead_of_error(service: RetrievalService, make_access) -> None:
    for mode in ("bm25", "hybrid"):
        result = _query(service, "风险评估", make_access(), retrieval_mode=mode)
        assert result.hits == []
        assert result.retrieval_mode == mode
        assert any("未启用" in item and item.startswith("RETRIEVAL_MODE_NOT_ENABLED") for item in result.warnings)

    reranked = _query(service, "风险评估", make_access(), rerank=True)
    assert reranked.hits
    assert reranked.rerank is True
    assert any(item.startswith("RERANK_NOT_ENABLED") for item in reranked.warnings)


def test_empty_and_special_query_return_empty_with_warning(service: RetrievalService, make_access) -> None:
    for raw in ("", "   ", "!!!???"):
        result = _query(service, raw, make_access())
        assert result.hits == []
        assert result.degraded is False
        assert result.warnings
        assert result.latency_ms >= 0


def test_result_model_contains_citation_and_timings(service: RetrievalService, make_access) -> None:
    result = _query(service, "风险评估", make_access(), top_k=3)
    payload = result.to_dict()
    assert payload["normalizedQuery"] == "风险评估"
    assert payload["retrievalMode"] == "vector"
    assert payload["rerank"] is False
    assert payload["degraded"] is False
    assert payload["timings"]["queryUnderstandingMs"] <= payload["latencyMs"] + 1
    assert payload["timings"]["totalMs"] == payload["latencyMs"]

    hit = payload["hits"][0]
    assert 0 <= hit["score"] <= 1
    assert hit["citation"]["chunkId"] == hit["chunkId"]
    assert hit["citation"]["documentId"] == hit["documentId"]
    assert hit["citation"]["clauseNo"] == "4.1.1"
    assert hit["citation"]["chapterPath"]


class _SlowSearcher:
    def search(self, query: SearchQuery):  # noqa: ANN001 - 测试桩
        time.sleep(0.2)
        return []


class _FailingSearcher:
    def search(self, query: SearchQuery):  # noqa: ANN001 - 测试桩
        raise ValueError("backend down")


def test_vector_timeout_degrades(encoder, audit: InMemoryAuditSink, make_access) -> None:
    service = RetrievalService(
        searcher=_SlowSearcher(),  # type: ignore[arg-type]
        encoder=encoder,
        audit=audit,
        config=RetrievalConfig(timeout_ms=50),
    )
    try:
        started = time.perf_counter()
        result = service.retrieve(RetrieveRequest("风险评估"), make_access())
        elapsed = time.perf_counter() - started
    finally:
        service.close()

    assert result.degraded is True
    assert result.degraded_reason == "VECTOR_TIMEOUT"
    assert result.hits == []
    assert any(item.startswith("VECTOR_TIMEOUT_DEGRADED") for item in result.warnings)
    assert elapsed < 0.5
    assert result.latency_ms <= 500


def test_backend_error_degrades(encoder, audit: InMemoryAuditSink, make_access) -> None:
    service = RetrievalService(
        searcher=_FailingSearcher(),  # type: ignore[arg-type]
        encoder=encoder,
        audit=audit,
        config=RetrievalConfig(timeout_ms=200),
    )
    try:
        result = service.retrieve(RetrieveRequest("风险评估"), make_access())
    finally:
        service.close()
    assert result.degraded is True
    assert result.degraded_reason == "VECTOR_BACKEND_ERROR"
    assert result.hits == []
    assert any(item.startswith("VECTOR_BACKEND_DEGRADED") for item in result.warnings)


def test_hashing_encoder_dimension_mismatch_degrades(
    searcher: InMemoryVectorSearcher, make_access
) -> None:
    service = RetrievalService(
        searcher=searcher,
        encoder=HashingQueryEncoder(dimension=8),
        audit=InMemoryAuditSink(),
        config=RetrievalConfig(timeout_ms=200),
    )
    try:
        result = service.retrieve(RetrieveRequest("风险评估"), make_access())
    finally:
        service.close()
    assert result.degraded is True
    assert result.hits == []


def test_record_from_chunk_maps_financial_metadata(encoder) -> None:
    chunk = {
        "chunk_id": "c1",
        "doc_id": "d1",
        "clause_no": "1.1",
        "chapter_path": "第一章",
        "content": "风险评估",
        "meta": {
            "standardNo": "JR/T 0088-2016",
            "issuer": "中国人民银行",
            "effectiveAt": "2017-06-01",
            "securityLevel": "PUBLIC",
        },
    }
    record = record_from_chunk(chunk, kb_id="kb_a", vector=encoder.encode("风险评估"))
    assert record.chunk_id == "c1"
    assert record.standard_no == "JR/T 0088-2016"
    assert record.effective_at == "2017-06-01"
    assert isinstance(record, VectorRecord)
