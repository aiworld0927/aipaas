"""检索 API：契约字段、错误码、未启用能力与鉴权头解析。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aipaas_retrieval import ACTION_DENIED, InMemoryAuditSink, RetrievalService
from query_worker.api.routers.retrieval import access_from_headers, get_retrieval_service_dep, router

HEADERS = {
    "X-User-Id": "u1",
    "X-Permissions": "retrieval:query",
    "X-Kb-Ids": "kb_a,kb_b,kb_c",
    "X-Trace-Id": "tr_test",
}


@pytest.fixture()
def client(service: RetrievalService):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_retrieval_service_dep] = lambda: service
    return TestClient(app)


def test_access_context_from_headers() -> None:
    ctx = access_from_headers(HEADERS)
    assert ctx.user_id == "u1"
    assert ctx.has_permission("retrieval:query")
    assert ctx.can_query_kb("kb_a")
    assert not ctx.can_query_kb("kb_forbidden")
    assert ctx.max_security_level == "PUBLIC"

    admin = access_from_headers({"X-Permissions": "*"})
    assert admin.all_kbs is True
    assert admin.authorized_kb_ids() is None

    internal = access_from_headers({"X-Security-Level": "internal"})
    assert internal.max_security_level == "INTERNAL"


def test_retrieve_contract_and_citation(client: TestClient) -> None:
    response = client.post("/retrieve", json={"query": "JR/T 0088", "topK": 2}, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["traceId"] == "tr_test"
    data = body["data"]
    assert data["query"] == "JR/T 0088"
    assert data["normalizedQuery"] == "JR/T 0088"
    assert data["retrievalMode"] == "vector"
    assert data["rerank"] is False
    assert data["latencyMs"] >= 0
    assert data["timings"]["totalMs"] == data["latencyMs"]
    assert data["hits"]
    hit = data["hits"][0]
    assert {"rank", "score", "chunkId", "documentId", "kbId", "text", "citation"} <= set(hit)
    assert hit["citation"]["chunkId"] == hit["chunkId"]


def test_retrieve_rejects_conflicting_kb_fields(client: TestClient) -> None:
    response = client.post(
        "/retrieve",
        json={"query": "风险评估", "kbId": "kb_a", "kbIds": ["kb_b"]},
        headers=HEADERS,
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "PARAM_INVALID"
    assert body["data"]["retryable"] is False


def test_retrieve_forbidden_kb(client: TestClient, audit: InMemoryAuditSink) -> None:
    response = client.post(
        "/retrieve",
        json={"query": "风险评估", "kbId": "kb_forbidden"},
        headers=HEADERS,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert any(entry["action"] == ACTION_DENIED for entry in audit.entries())


def test_retrieve_without_permission_header(client: TestClient) -> None:
    response = client.post("/retrieve", json={"query": "风险评估"}, headers={"X-Kb-Ids": "kb_a"})
    assert response.status_code == 403


def test_retrieve_p1_mode_returns_not_enabled(client: TestClient) -> None:
    response = client.post(
        "/retrieve",
        json={"query": "风险评估", "retrievalMode": "hybrid", "rerank": False},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["hits"] == []
    assert any("未启用" in item for item in data["warnings"])


def test_retrieve_rerank_not_enabled_warning(client: TestClient) -> None:
    response = client.post(
        "/retrieve",
        json={"query": "风险评估", "rerank": True},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["hits"]
    assert data["rerank"] is True
    assert any(item.startswith("RERANK_NOT_ENABLED") for item in data["warnings"])


def test_query_understanding_endpoint(client: TestClient) -> None:
    response = client.post(
        "/retrieve/query-understanding",
        json={"query": "jrt 0088-2016 第4.1.1条 风险评估"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["standardNo"] == "JR/T 0088-2016"
    assert data["clauseNo"] == "4.1.1"
    assert data["normalizedQuery"] == "JR/T 0088-2016 4.1.1 风险评估"
    assert data["latencyMs"] >= 0

    empty = client.post("/retrieve/query-understanding", json={"query": ""}).json()["data"]
    assert empty["normalizedQuery"] == ""
    assert any(item.startswith("QUERY_EMPTY") for item in empty["warnings"])


def test_retrieve_overlong_query_is_truncated(client: TestClient) -> None:
    response = client.post("/retrieve", json={"query": "风险评估" * 1000}, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["normalizedQuery"]) <= 2000
    assert any(item.startswith("QUERY_TRUNCATED") for item in data["warnings"])
