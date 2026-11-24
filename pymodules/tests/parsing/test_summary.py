"""#405 标准简介与条目化条款清单测试：五要素、缺失标注、可追溯、版本化。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aipaas_pipeline.chunking import InMemoryChunkStore
from aipaas_pipeline.repository import InMemoryParseResultRepository
from aipaas_pipeline.samples import pdf_gbt_35273
from aipaas_pipeline.service import ParseService
from aipaas_pipeline.storage import InMemoryArtifactStore
from aipaas_pipeline.summary import (
    MISSING,
    InMemorySummaryStore,
    SummaryForbidden,
    SummaryService,
)


@pytest.fixture()
def parse_service() -> ParseService:
    service = ParseService(
        artifacts=InMemoryArtifactStore(),
        chunks=InMemoryChunkStore(),
        results=InMemoryParseResultRepository(),
    )
    service.parse(
        pdf_gbt_35273(),
        doc_id="doc_summary",
        tenant_id="tenant-a",
        filename="GBT-35273-2020.pdf",
    )
    return service


@pytest.fixture()
def summary_service(parse_service: ParseService) -> SummaryService:
    return SummaryService(parse_service, InMemorySummaryStore())


def _elements(payload: dict) -> dict[str, dict]:
    return {item["key"]: item for item in payload["elements"]}


def test_summary_has_five_elements_and_missing_marker(summary_service: SummaryService):
    payload = summary_service.generate("doc_summary", tenant_id="tenant-a")
    elements = _elements(payload)
    assert list(elements) == ["standard_no", "standard_name", "scope", "main_content", "effective_date"]
    assert elements["standard_no"]["value"] == "GB/T 35273-2020"
    assert "个人信息安全规范" in elements["standard_name"]["value"]
    assert "本文件规定" in elements["scope"]["value"]
    assert elements["main_content"]["value"].startswith("本文件共 ")
    assert "条" in elements["main_content"]["value"]
    # 样例未给出实施日期：必须明确标注“未识别”，不得编造
    assert elements["effective_date"]["value"] == MISSING
    assert payload["effectiveAt"] is None


def test_elements_are_traceable_to_source_clause(summary_service: SummaryService):
    payload = summary_service.generate("doc_summary")
    elements = _elements(payload)
    scope = elements["scope"]
    assert scope["sourceClauseNo"]
    assert scope["sourceQuote"] and scope["sourceQuote"] in scope["value"]
    main = elements["main_content"]
    assert main["sourceClauseNo"]
    assert main["sourceQuote"]
    assert payload["documentVersion"] >= 1
    assert payload["promptTemplateVersion"] == "summary-extract-v1"
    assert 0.0 <= payload["confidence"] <= 1.0
    assert payload["trace"]["missingElements"] == ["effective_date"]


def test_idempotent_latest_and_regenerate_new_version(summary_service: SummaryService):
    first = summary_service.generate("doc_summary")
    same = summary_service.generate("doc_summary")
    assert same["id"] == first["id"]
    regenerated = summary_service.generate("doc_summary", regenerate=True)
    assert regenerated["id"] != first["id"]
    assert regenerated["summaryVersion"] == 2
    assert summary_service.latest("doc_summary")["id"] == regenerated["id"]
    assert summary_service.versions("doc_summary") == [1, 2]


def test_cross_tenant_summary_is_forbidden(summary_service: SummaryService):
    with pytest.raises(SummaryForbidden):
        summary_service.generate("doc_summary", tenant_id="tenant-b")


def test_clause_list_matches_parsed_clauses(parse_service: ParseService):
    payload = parse_service.list_clauses("doc_summary", page=1, page_size=2)
    assert payload is not None
    assert payload["clauseCount"] == len(parse_service.get_document("doc_summary").clauses)
    assert payload["total"] == payload["clauseCount"]
    assert len(payload["items"]) == 2
    first = payload["items"][0]
    assert first["clauseNo"] and first["title"] and first["chapterPath"]
    assert first["chunkId"]


def test_summary_api_returns_contract_payload(summary_service: SummaryService):
    from app_ingest_worker.api.dependencies import get_summary_service
    from app_ingest_worker.api.routers import summary

    app = FastAPI()
    app.include_router(summary.router)
    app.dependency_overrides[get_summary_service] = lambda: summary_service
    client = TestClient(app)

    created = client.post(
        "/api/v1/documents/doc_summary/summaries",
        json={"regenerate": True},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["code"] == 200
    assert body["data"]["documentId"] == "doc_summary"
    assert len(body["data"]["elements"]) == 5

    latest = client.get("/api/v1/documents/doc_summary/summaries/latest")
    assert latest.status_code == 200
    assert latest.json()["data"]["id"] == body["data"]["id"]

    forbidden = client.get(
        "/api/v1/documents/doc_summary/summaries/latest",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert forbidden.status_code == 403
