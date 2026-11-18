"""#173/#175/#177 可观测测试：Trace 传播、指标文本格式、请求日志脱敏。"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aipaas_common.core.log import logger
from aipaas_common.core.observability import (
    format_traceparent,
    install_observability,
    mark_generation,
    mark_retrieval,
    metrics,
    parse_traceparent,
    reset_for_tests,
    span,
    trace_snapshot,
)

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN = "00f067aa0ba902b7"


@pytest.fixture(autouse=True)
def clean_observability():
    reset_for_tests()
    yield
    reset_for_tests()


def make_app() -> FastAPI:
    app = FastAPI()
    install_observability(app, "svc-test")

    @app.get("/echo")
    async def echo() -> dict[str, bool]:
        with span("retrieve.query", tenantId="default") as current:
            current.attribute("hits", 2)
        mark_retrieval(True, 0.02, service="svc-test")
        mark_generation(False, 0.03, service="svc-test")
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        with span("generate.answer"):
            raise RuntimeError("model failed")

    return app


def test_traceparent_roundtrip_and_validation() -> None:
    parsed = parse_traceparent(f"00-{TRACE_ID}-{PARENT_SPAN}-01")
    assert parsed == (TRACE_ID, PARENT_SPAN, "01")
    assert parse_traceparent("00-invalid-01") is None
    generated = format_traceparent("tr_abc", "span")
    assert generated.startswith("00-") and generated.endswith("-01")
    assert len(generated.split("-")[1]) == 32


def test_trace_is_propagated_and_queryable_by_trace_id() -> None:
    client = TestClient(make_app())
    response = client.get(
        "/echo",
        headers={
            "traceparent": f"00-{TRACE_ID}-{PARENT_SPAN}-01",
            "X-Request-Id": "req-1",
            "X-User-Id": "usr_1",
            "X-Tenant-Id": "default",
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Trace-Id"] == TRACE_ID

    snapshot = trace_snapshot(TRACE_ID)
    names = [item["name"] for item in snapshot["spans"]]
    assert "GET /echo" in names and "retrieve.query" in names
    child = next(item for item in snapshot["spans"] if item["name"] == "retrieve.query")
    root = next(item for item in snapshot["spans"] if item["name"] == "GET /echo")
    assert child["parentSpanId"] == root["spanId"]
    assert child["durationMs"] >= 0

    queried = client.get(f"/api/v1/observability/traces/{TRACE_ID}")
    assert queried.status_code == 200
    assert queried.json()["data"]["spanCount"] >= 2


def test_error_span_marks_status_and_does_not_hide_exception() -> None:
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/boom", headers={"X-Trace-Id": "tr_boom"})
    assert response.status_code == 500
    spans = trace_snapshot("tr_boom")["spans"]
    failed = [item for item in spans if item["status"] == "ERROR"]
    assert failed and failed[0]["attributes"].get("error.type") == "RuntimeError"


def test_metrics_expose_qps_latency_hit_and_refusal() -> None:
    client = TestClient(make_app())
    client.get("/echo")
    text = client.get("/metrics").text
    assert 'aipaas_http_requests_total{service="svc-test",method="GET",path="/echo",status="200"} 1' in text
    assert "aipaas_http_request_duration_seconds_bucket" in text
    assert 'aipaas_retrieval_hits_total{service="svc-test"} 1' in text
    assert 'aipaas_retrieval_queries_total{service="svc-test"} 1' in text
    assert 'aipaas_generation_requests_total{service="svc-test"} 1' in text
    assert "aipaas_process_resident_memory_bytes" in text


def test_request_log_contains_required_fields_without_sensitive_values() -> None:
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), format="{message}")
    try:
        client = TestClient(make_app())
        client.get(
            "/echo?token=super-secret&password=abc",
            headers={
                "Authorization": "Bearer secret-token",
                "X-User-Id": "usr_9",
                "X-Tenant-Id": "default",
                "X-Forwarded-For": "10.1.2.3",
            },
        )
    finally:
        logger.remove(sink_id)

    request_lines = [line for line in records if '"kind":"request"' in line]
    assert request_lines, records
    entry = json.loads(request_lines[-1])
    assert entry["service"] == "svc-test"
    assert entry["path"] == "/echo"
    assert entry["method"] == "GET"
    assert entry["status"] == 200
    assert entry["userId"] == "usr_9"
    assert entry["durationMs"] >= 0
    assert entry["clientIp"] == "10.1.2.0"
    serialized = json.dumps(entry)
    assert "super-secret" not in serialized
    assert "secret-token" not in serialized
    assert "password" not in serialized
    assert "authorization" not in serialized.lower()


def test_metric_labels_are_escaped() -> None:
    metrics.http_requests.inc(service='svc"x', method="GET", path="/a\nb", status="200")
    rendered = metrics.render("svc-test")
    assert 'service="svc\\"x"' in rendered
    assert 'path="/a\\nb"' in rendered
