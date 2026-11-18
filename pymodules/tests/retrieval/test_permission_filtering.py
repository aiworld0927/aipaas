"""#88 行级权限：召回阶段过滤、越权审计、密级校验与实时生效。"""

from __future__ import annotations

import pytest

from aipaas_retrieval import (
    ACTION_DENIED,
    ACTION_QUERY,
    InMemoryAuditSink,
    PermissionDenied,
    RetrievalConfig,
    RetrievalService,
    RetrieveRequest,
    SearchHit,
)


def _query(service: RetrievalService, query: str, ctx, **kwargs):
    return service.retrieve(RetrieveRequest(query=query, **kwargs), ctx)


def test_unauthorized_kb_is_filtered_before_scoring(
    service: RetrievalService, audit: InMemoryAuditSink, make_access
) -> None:
    result = _query(service, "绝密 风险评估 未授权", make_access())
    assert result.hits
    assert all(hit.kb_id != "kb_forbidden" for hit in result.hits)
    # 未发生显式越权尝试时，只记录成功检索审计
    assert [entry["action"] for entry in audit.entries()] == [ACTION_QUERY]


def test_explicit_forbidden_kb_is_denied_and_audited(
    service: RetrievalService, audit: InMemoryAuditSink, make_access
) -> None:
    with pytest.raises(PermissionDenied) as excinfo:
        _query(service, "风险评估", make_access(), kb_id="kb_forbidden")
    assert excinfo.value.reason == "KB_FORBIDDEN"
    denied = [entry for entry in audit.entries() if entry["action"] == ACTION_DENIED]
    assert len(denied) == 1
    assert denied[0]["reason"] == "KB_FORBIDDEN"
    assert denied[0]["kbId"] == "kb_forbidden"
    assert denied[0]["allowed"] is False


def test_missing_permission_is_denied_and_audited(
    service: RetrievalService, audit: InMemoryAuditSink, make_access
) -> None:
    with pytest.raises(PermissionDenied) as excinfo:
        _query(service, "风险评估", make_access(permissions=set()))
    assert excinfo.value.reason == "PERMISSION_MISSING"
    assert any(entry["action"] == ACTION_DENIED and entry["reason"] == "PERMISSION_MISSING"
               for entry in audit.entries())


def test_security_level_is_enforced(service: RetrievalService, make_access) -> None:
    public = _query(service, "风险评估", make_access(max_level="PUBLIC"))
    assert "a-4.2" not in {hit.chunk_id for hit in public.hits}

    internal = _query(service, "风险评估", make_access(max_level="INTERNAL"))
    assert "a-4.2" in {hit.chunk_id for hit in internal.hits}

    secret = _query(service, "风险评估", make_access(max_level="SECRET"))
    assert "x-1.1" not in {hit.chunk_id for hit in secret.hits}  # kb 未授权仍不可见

    admin = _query(service, "风险评估", make_access(permissions={"*"}, all_kbs=True, max_level="PUBLIC"))
    assert "x-1.2" in {hit.chunk_id for hit in admin.hits}
    assert "x-1.1" not in {hit.chunk_id for hit in admin.hits}


class _LeakySearcher:
    """模拟后端 bug：直接返回越权与超密级记录，验证服务层兜底。"""

    def __init__(self, records) -> None:
        self._records = records

    def search(self, query):  # noqa: ANN001 - 测试桩
        return [SearchHit(record=record, score=0.99) for record in self._records]


def test_leak_guard_drops_backend_leak_and_audits(
    records, encoder, make_access
) -> None:
    leaked = [record for record in records if record.chunk_id in {"x-1.2", "x-1.1", "a-4.2"}]
    audit = InMemoryAuditSink()
    service = RetrievalService(
        searcher=_LeakySearcher(leaked),
        encoder=encoder,
        audit=audit,
        config=RetrievalConfig(timeout_ms=200),
    )
    try:
        result = service.retrieve(RetrieveRequest("风险评估"), make_access())
    finally:
        service.close()

    assert result.hits == []
    denied = [entry for entry in audit.entries() if entry["action"] == ACTION_DENIED]
    assert {entry["detail"]["chunkId"] for entry in denied} == {"x-1.2", "x-1.1", "a-4.2"}
    assert all(entry["reason"] == "ROW_FILTERED" for entry in denied)


def test_permission_changes_take_effect_immediately(service: RetrievalService, make_access) -> None:
    # 1) 仅授权 kb_a：反洗钱条款（kb_b）不可见
    only_a = _query(service, "反洗钱 客户尽职调查", make_access(kb_ids=("kb_a",)))
    assert all(hit.kb_id != "kb_b" for hit in only_a.hits)

    # 2) 授信 kb_b 后下一请求立即出现
    with_b = _query(service, "反洗钱 客户尽职调查", make_access(kb_ids=("kb_a", "kb_b")))
    assert "b-5.1" in {hit.chunk_id for hit in with_b.hits}

    # 3) 收回后立即不可见
    revoked = _query(service, "反洗钱 客户尽职调查", make_access(kb_ids=("kb_a",)))
    assert all(hit.kb_id != "kb_b" for hit in revoked.hits)


def test_successful_query_is_audited_with_metrics(
    service: RetrievalService, audit: InMemoryAuditSink, make_access
) -> None:
    _query(service, "风险评估", make_access(), top_k=3)
    entries = audit.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == ACTION_QUERY
    assert entry["allowed"] is True
    assert entry["detail"]["hitCount"] > 0
    assert entry["detail"]["latencyMs"] >= 0
    assert entry["traceId"] is None
