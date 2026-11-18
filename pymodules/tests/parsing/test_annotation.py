"""#57 元数据标注：六要素、规则优先 + LLM 兜底、过滤、审计、chunk.meta 预留字段。"""
from __future__ import annotations

import pytest

from aipaas_pipeline.annotation import (
    RESERVED_META_FIELDS,
    AnnotationService,
    MetaFilter,
    annotate_document,
    evaluate_accuracy,
)
from aipaas_pipeline.chunking import ChunkConfig, build_chunks
from aipaas_pipeline.models import Clause, ParsedDocument
from aipaas_pipeline.samples import pdf_gbt_35273
from aipaas_pipeline.service import ParseService


def make_document(
    doc_id: str = "doc-meta",
    *,
    standard_no: str | None = "JR/T 0197-2020",
    title: str = "JR/T 0197-2020 金融数据安全 数据安全分级指南",
    issuer: str = "中国人民银行",
    effective: str = "2021年6月1日",
    filename: str = "JRT-0197-2020.pdf",
) -> ParsedDocument:
    raw = f"{title}\n发布机构：{issuer}\n实施日期：{effective}\n4.1 范围\n本标准规定了金融数据安全分级要求。"
    return ParsedDocument(
        doc_id=doc_id,
        parser="test",
        success=True,
        title=title,
        standard_no=standard_no,
        chapter_tree=[],
        clauses=[
            Clause("4.1", "范围", "本标准规定了金融数据安全分级要求。", "第4章 范围", 3, page_no=1),
            Clause("4.2", "术语", "下列术语适用于本文件。", "第4章 范围", 3, page_no=1),
        ],
        tables=[],
        raw_text=raw,
        pages=1,
        extras={"filename": filename},
    )


# --------------------------------------------------------------------------- 六要素


def test_six_elements_are_extracted_from_rules() -> None:
    meta = annotate_document(make_document())
    assert meta["standardNo"] == "JR/T 0197-2020"
    assert meta["standardName"] == "金融数据安全 数据安全分级指南"
    assert meta["issuer"] == "中国人民银行"
    assert meta["effectiveAt"] == "2021-06-01"
    assert meta["sourceDocument"] == "JRT-0197-2020.pdf"
    assert meta["clauseCount"] == 2
    assert meta["confidence"] == 1.0
    assert meta["source"] == "AUTO_EXTRACTED"
    for key in ("standardNo", "standardName", "issuer", "effectiveAt", "sourceDocument"):
        assert meta["evidence"][key]["extractor"] == "rule"
        assert meta["evidence"][key]["snippet"]


def test_chunks_carry_clause_no_and_reserved_meta_fields() -> None:
    document = make_document()
    service = AnnotationService()
    meta = service.annotate(document)
    chunks = build_chunks(document, ChunkConfig("itemized"))
    service.decorate_chunks(document, chunks, meta)
    first = chunks[0].to_dict()
    assert first["clauseNo"] == "4.1"
    assert first["meta"]["clauseNo"] == "4.1"
    assert first["meta"]["documentMetadata"]["standardNo"] == "JR/T 0197-2020"
    assert first["meta"]["documentMetadata"]["issuer"] == "中国人民银行"
    for key in RESERVED_META_FIELDS:
        assert key in first["meta"] and first["meta"][key] is None


def test_rules_first_and_llm_only_fills_missing_with_trace() -> None:
    document = make_document(issuer="", effective="")
    document.raw_text = "本标准正文不包含发布机构与实施日期。"
    calls: list[list[str]] = []

    def llm(doc, missing):
        calls.append(list(missing))
        return {"issuer": "国家标准化管理委员会", "effectiveAt": "2022-01-01", "__model__": "test-llm"}

    meta = annotate_document(document, llm)
    assert calls == [["issuer", "effectiveAt"]], "仅对缺失字段调用 LLM"
    assert meta["standardNo"] == "JR/T 0197-2020", "规则命中的字段不得被 LLM 覆盖"
    assert meta["issuer"] == "国家标准化管理委员会"
    assert meta["effectiveAt"] == "2022-01-01"
    evidence = meta["evidence"]["issuer"]
    assert evidence["extractor"] == "llm"
    assert evidence["model"] == "test-llm"
    assert len(evidence["promptDigest"]) == 64
    assert len(evidence["responseDigest"]) == 64


def test_llm_failure_keeps_rule_result_and_is_recorded() -> None:
    document = make_document(issuer="", effective="")
    document.raw_text = "本标准正文。"

    def broken(doc, missing):
        raise RuntimeError("llm offline")

    meta = annotate_document(document, broken)
    assert meta["standardNo"] == "JR/T 0197-2020"
    assert meta["evidence"]["__llm_error__"]["error"].startswith("RuntimeError")
    assert meta["confidence"] < 1.0


# --------------------------------------------------------------------------- 过滤


def test_filter_by_standard_no_issuer_and_effective_time() -> None:
    service = AnnotationService()
    service.store.save(service.annotate(make_document("d1")))
    service.store.save(service.annotate(make_document(
        "d2", standard_no="GB/T 35273-2020", title="GB/T 35273-2020 信息安全技术 个人信息安全规范",
        issuer="国家市场监督管理总局", effective="2020年10月1日", filename="GBT.pdf",
    )))
    assert [m["documentId"] for m in service.query(MetaFilter(standard_no="jr/t 0197-2020"))] == ["d1"]
    assert [m["documentId"] for m in service.query(MetaFilter(issuer="国家市场"))] == ["d2"]
    assert [m["documentId"] for m in service.query(MetaFilter(effective_from="2021-01-01"))] == ["d1"]
    assert [m["documentId"] for m in service.query(MetaFilter(effective_to="2020-12-31"))] == ["d2"]
    assert [m["documentId"] for m in service.query(MetaFilter(keyword="数据安全"))] == ["d1"]
    assert service.query(MetaFilter(standard_no="GB/T 99999-2099")) == []


def test_manual_correction_is_auditable() -> None:
    service = AnnotationService()
    document = make_document()
    service.annotate_and_save(document, actor="parser")
    updated = service.update("doc-meta", {"issuer": "国家金融监督管理总局"}, actor="reviewer")
    assert updated["issuer"] == "国家金融监督管理总局"
    assert updated["source"] == "MANUAL_CORRECTED"
    assert updated["clauseCount"] == 2
    entries = service.audit("doc-meta")
    assert [entry["action"] for entry in entries] == ["meta.annotate", "meta.update"]
    assert entries[1]["actor"] == "reviewer"
    assert entries[1]["before"]["issuer"] == "中国人民银行"
    assert entries[1]["after"]["issuer"] == "国家金融监督管理总局"
    assert [entry["seq"] for entry in entries] == [1, 2], "审计序号只增不改"

    with pytest.raises(ValueError):
        service.update("doc-meta", {"standardNoX": "x"})
    with pytest.raises(ValueError):
        service.update("doc-meta", {"effectiveAt": "not-a-date"})
    with pytest.raises(KeyError):
        service.update("missing", {"issuer": "x"})


# --------------------------------------------------------------------------- 准确率


def test_accuracy_on_labelled_cases_is_at_least_95_percent() -> None:
    issuers = ["中国人民银行", "国家标准化管理委员会", "全国金融标准化技术委员会", "国家市场监督管理总局"]
    cases = []
    for index in range(24):
        number = f"JR/T {1000 + index}-20{20 + index % 4}"
        title = f"{number} 金融标准样例{index}"
        issuer = issuers[index % len(issuers)]
        effective = f"20{20 + index % 4}年{index % 12 + 1}月{index % 27 + 1}日"
        document = make_document(
            f"acc-{index}", standard_no=number, title=title, issuer=issuer,
            effective=effective, filename=f"case-{index}.pdf",
        )
        cases.append(
            (
                document,
                {
                    "standardNo": number,
                    "standardName": title.replace(number, "").strip(),
                    "issuer": issuer,
                    "effectiveAt": f"20{20 + index % 4}-{index % 12 + 1:02d}-{index % 27 + 1:02d}",
                    "sourceDocument": f"case-{index}.pdf",
                },
            )
        )
    result = evaluate_accuracy(cases)
    assert result["accuracy"] >= 0.95, result
    assert all(value is None or value >= 0.95 for value in result["perField"].values()), result


# --------------------------------------------------------------------------- 解析集成


def test_parse_pipeline_persists_metadata_and_decorates_chunks(service: ParseService) -> None:
    document = service.parse(pdf_gbt_35273(), doc_id="doc-parse-meta", filename="GBT-35273-2020.pdf")
    assert document.success
    meta = service.get_metadata("doc-parse-meta")
    assert meta is not None
    assert meta["standardNo"] == "GB/T 35273-2020"
    assert meta["source"] == "AUTO_EXTRACTED"
    assert meta["clauseCount"] == len(document.clauses)
    assert meta["evidence"]["standardNo"]["snippet"]

    base = f"parse/default/doc-parse-meta/v{document.version}"
    assert service.artifacts.exists(f"{base}/metadata.json")
    assert service.artifacts.exists("parse/default/doc-parse-meta/latest-metadata.json")

    chunks = service.get_chunks_artifact("doc-parse-meta")["chunks"]
    assert chunks and all("documentMetadata" in chunk["meta"] for chunk in chunks)
    assert all(chunk["meta"].get("clauseNo") == chunk.get("clauseNo") for chunk in chunks)
    for key in RESERVED_META_FIELDS:
        assert all(key in chunk["meta"] for chunk in chunks)

    filtered = service.query_metadata(MetaFilter(standard_no="GB/T 35273-2020"))
    assert [item["documentId"] for item in filtered] == ["doc-parse-meta"]


# --------------------------------------------------------------------------- HTTP API


def _client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ingest_worker.api.dependencies import get_parse_service
    from ingest_worker.api.routers import metadata, parse

    app = FastAPI()
    app.include_router(parse.router)
    app.include_router(metadata.router)
    app.dependency_overrides[get_parse_service] = lambda: service
    return TestClient(app)


def test_metadata_api_query_filter_correction_and_audit(service) -> None:
    client = _client(service)
    uploaded = client.post(
        "/api/v1/documents/api-meta:parse",
        files={"file": ("GBT-35273-2020.pdf", pdf_gbt_35273(), "application/pdf")},
    )
    assert uploaded.status_code == 200

    got = client.get("/api/v1/documents/api-meta/metadata")
    assert got.status_code == 200
    meta = got.json()["data"]
    assert meta["standardNo"] == "GB/T 35273-2020"
    assert meta["evidence"]["standardNo"]["snippet"]

    corrected = client.put(
        "/api/v1/documents/api-meta/metadata",
        json={"issuer": "国家市场监督管理总局"},
        headers={"X-User-Id": "reviewer-1"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["data"]["source"] == "MANUAL_CORRECTED"

    audit = client.get("/api/v1/documents/api-meta/metadata/audit").json()["data"]["entries"]
    assert [entry["action"] for entry in audit] == ["meta.annotate", "meta.update"]
    assert audit[-1]["actor"] == "reviewer-1"

    matched = client.get(
        "/api/v1/metadata:query",
        params={"standardNo": "GB/T 35273-2020", "issuer": "国家市场"},
    ).json()["data"]
    assert matched["total"] == 1 and matched["items"][0]["documentId"] == "api-meta"

    missed = client.get("/api/v1/metadata:query", params={"standardNo": "GB/T 00000-2000"}).json()["data"]
    assert missed["total"] == 0

    bad = client.put("/api/v1/documents/api-meta/metadata", json={"unknownField": "x"})
    assert bad.status_code == 422
    missing = client.get("/api/v1/documents/none/metadata")
    assert missing.status_code == 404
