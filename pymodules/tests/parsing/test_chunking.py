from __future__ import annotations

import hashlib
import json

import pytest

from aipaas_pipeline.chunking import (
    ChunkConfig,
    ChunkingInvariantError,
    assert_chunk_invariants,
    build_chunks,
    chunk_invariants,
    dedupe_chunks,
)
from aipaas_pipeline.models import Clause, ParsedDocument, SectionNode, TableBlock
from aipaas_pipeline.samples import md_jrt_code


def make_document(*, clauses: list[Clause] | None = None) -> ParsedDocument:
    if clauses is None:
        clauses = [
            Clause("4.1.1", "总则", "组织应建立数据分级制度。数据分级应覆盖全部数据资产。",
                   "第4章 数据分级 > 4.1 要求", 3, page_no=1, section_id="s3"),
            Clause("4.1.2", "最小必要", "收集个人信息应当遵循最小必要原则。不得过度收集。",
                   "第4章 数据分级 > 4.1 要求", 3, page_no=2, section_id="s3"),
            Clause("4.1.3", "告知同意", "应明示处理目的和方式。",
                   "第4章 数据分级 > 4.1 要求", 3, page_no=2, section_id="s4"),
        ]
    return ParsedDocument(
        doc_id="doc-chunk",
        parser="test",
        success=True,
        title="测试标准",
        standard_no="JR/T 0001-2024",
        chapter_tree=[
            SectionNode("s1", "第4章 数据分级", 1, clause_no="4", children=[
                SectionNode("s2", "4.1 要求", 2, clause_no="4.1", children=[
                    SectionNode("s3", "4.1.1 总则", 3, clause_no="4.1.1"),
                    SectionNode("s4", "4.1.3 告知同意", 3, clause_no="4.1.3"),
                ]),
            ])
        ],
        clauses=clauses,
        tables=[TableBlock("tbl_1", 1, [["条款号", "要求"], ["4.1.1", "总则"]], caption="表1 要求")],
        raw_text="4.1.1 总则\n组织应建立数据分级制度。",
        pages=2,
        extras={"codeBlocks": [{"language": "text", "content": "SELECT  *\n  FROM  grade\n",
                                "startLine": 10, "endLine": 13}]},
    )


def chunk_payload(chunk) -> dict:
    return chunk.to_dict()


# --------------------------------------------------------------------------- 统一模型 / 配置


def test_unified_chunk_model_fields_and_hash() -> None:
    document = make_document()
    chunks = build_chunks(document, ChunkConfig("itemized"))
    payload = chunk_payload(chunks[0])
    required = {"chunkId", "docId", "clauseNo", "clauseTitle", "chapterPath",
                "content", "tokenCount", "hash", "meta"}
    assert required <= set(payload)
    assert payload["docId"] == "doc-chunk"
    assert payload["clauseNo"] == "4.1.1"
    assert payload["tokenCount"] > 0
    assert payload["hash"] == hashlib.sha256(payload["content"].encode("utf-8")).hexdigest()
    assert payload["meta"]["strategy"] == "itemized"


def test_chunk_config_validates_and_versions() -> None:
    base = ChunkConfig("fixed", size=100, overlap=10)
    assert base.config_version == ChunkConfig("fixed", size=100, overlap=10).config_version
    assert base.config_version != ChunkConfig("fixed", size=200, overlap=10).config_version
    assert ChunkConfig.from_mapping(
        {"chunkStrategy": "itemized", "chunkSize": 50, "chunkOverlap": 0}
    ).strategy == "itemized"

    with pytest.raises(ValueError):
        ChunkConfig("unknown")
    with pytest.raises(ValueError):
        ChunkConfig("fixed", size=10, overlap=0)
    with pytest.raises(ValueError):
        ChunkConfig("fixed", size=100, overlap=100)


# --------------------------------------------------------------------------- #36 固定长度


def test_fixed_chunk_count_matches_config() -> None:
    document = make_document(clauses=[])
    document.raw_text = "数" * 320
    config = ChunkConfig("fixed", size=32, overlap=0, sentence_aligned=False)
    chunks = build_chunks(document, config)
    assert len(chunks) == 10
    assert all(chunk.token_count <= config.size for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == "数" * 320


def test_fixed_chunks_support_overlap_window() -> None:
    sentences = [f"第{index}条说明内容较短。" for index in range(1, 13)]
    document = make_document(clauses=[])
    document.raw_text = "".join(sentences)
    config = ChunkConfig("fixed", size=32, overlap=12, sentence_aligned=True)
    chunks = build_chunks(document, config)
    assert len(chunks) > 1
    assert any(chunk.meta["overlapTokens"] > 0 for chunk in chunks[1:])
    for previous, current in zip(chunks, chunks[1:]):
        assert current.meta["startOffset"] < previous.meta["endOffset"], "窗口必须存在重叠"


def test_fixed_sentence_alignment_avoids_broken_boundaries() -> None:
    document = make_document(clauses=[])
    document.raw_text = "第一句内容。第二句内容。第三句内容。第四句内容。第五句内容。" * 3
    aligned = build_chunks(document, ChunkConfig("fixed", size=32, overlap=0, sentence_aligned=True))
    assert len(aligned) > 1
    for chunk in aligned[:-1]:
        assert chunk.content.endswith("。"), "按句对齐时边界应落在句末"

    unaligned = build_chunks(document, ChunkConfig("fixed", size=32, overlap=0, sentence_aligned=False))
    assert [chunk.content for chunk in aligned] != [chunk.content for chunk in unaligned]


def test_fixed_boundaries_are_previewable_and_stable() -> None:
    document = make_document()
    config = ChunkConfig("fixed", size=40, overlap=8)
    first = build_chunks(document, config)
    second = build_chunks(document, config)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    offsets = [(chunk.meta["startOffset"], chunk.meta["endOffset"]) for chunk in first]
    assert offsets == sorted(offsets)
    for chunk in first:
        assert chunk.meta["startOffset"] < chunk.meta["endOffset"]
        assert chunk.meta["preview"] == chunk.content[:80]
        assert chunk.meta["clauseNos"], "固定分块也应能关联到条款编号"


# --------------------------------------------------------------------------- #37 结构化


def test_structured_keeps_clause_units_and_associates_number() -> None:
    document = make_document()
    config = ChunkConfig("structured")
    chunks = build_chunks(document, config)
    clause_chunks = [chunk for chunk in chunks if chunk.chunk_type == "clause"]
    assert len(clause_chunks) == len(document.clauses)
    for clause, chunk in zip(document.clauses, clause_chunks):
        assert clause.text in chunk.content, "条款单元不得被拆散"
        assert chunk.content.startswith(clause.clause_no)
        assert chunk.clause_title == clause.title
        assert chunk.chapter_path == clause.chapter_path
        assert chunk.meta["level"] == clause.level


def test_structured_never_splits_long_clause() -> None:
    text = "应" * 900
    long_clause = Clause("5.1.1", "超长条款", text, "第5章", 3, page_no=1, section_id="s9")
    document = make_document(clauses=[long_clause])
    document.tables = []
    document.extras["codeBlocks"] = []
    chunks = build_chunks(document, ChunkConfig("structured", size=32, overlap=0))
    assert len(chunks) == 1
    assert chunks[0].content == "5.1.1 超长条款\n" + text
    assert chunks[0].token_count > 32, "结构化分块不得按 size 拆散条款"


def test_structured_includes_code_and_table_chunks() -> None:
    document = make_document()
    chunks = build_chunks(document, ChunkConfig("structured"))
    code = [chunk for chunk in chunks if chunk.chunk_type == "code"]
    table = [chunk for chunk in chunks if chunk.chunk_type == "table"]
    assert len(code) == 1 and code[0].content == "SELECT  *\n  FROM  grade\n"
    assert len(table) == 1 and table[0].meta["tableId"] == "tbl_1"
    assert "4.1.1" in table[0].content


# --------------------------------------------------------------------------- #404 条目化


def test_itemized_count_equals_clause_count() -> None:
    for document in (make_document(), _parse_sample()):
        config = ChunkConfig("itemized")
        chunks = build_chunks(document, config)
        assert len(chunks) == len(document.clauses)
        assert all(chunk.chunk_type == "clause" for chunk in chunks)
        assert [chunk.clause_no for chunk in chunks] == [
            clause.clause_no for clause in document.clauses
        ]
        invariants = assert_chunk_invariants(document, chunks, config)
        assert invariants["countMatchesClauses"] is True


def test_itemized_chunk_contains_number_and_body() -> None:
    document = make_document()
    chunks = build_chunks(document, ChunkConfig("itemized"))
    assert chunks[0].content == "4.1.1 总则\n组织应建立数据分级制度。数据分级应覆盖全部数据资产。"
    assert chunks[0].chapter_path == "第4章 数据分级 > 4.1 要求"
    assert chunks[0].meta["anchor"] == "clause_no"
    assert chunks[0].meta["sectionId"] == "s3"


def test_invariant_violation_raises() -> None:
    document = make_document()
    config = ChunkConfig("itemized")
    chunks = build_chunks(document, config)
    with pytest.raises(ChunkingInvariantError):
        assert_chunk_invariants(document, chunks[:-1], config)
    with pytest.raises(ChunkingInvariantError):
        assert_chunk_invariants(
            document,
            build_chunks(document, ChunkConfig("structured", include_code_blocks=False)),
            ChunkConfig("itemized"),
        )


# --------------------------------------------------------------------------- 幂等


def test_repeated_chunking_is_idempotent_by_hash() -> None:
    document = make_document()
    config = ChunkConfig("structured")
    first = build_chunks(document, config)
    second = build_chunks(document, config)
    assert [(chunk.chunk_id, chunk.content_hash) for chunk in first] == [
        (chunk.chunk_id, chunk.content_hash) for chunk in second
    ]
    assert dedupe_chunks(first + second) == first


def test_identical_content_across_clauses_is_not_dropped() -> None:
    clauses = [
        Clause("4.1.1", "总则", "应建立制度。", "第4章", 3, section_id="s1"),
        Clause("4.1.1", "总则", "应建立制度。", "第4章", 3, section_id="s2"),
    ]
    document = make_document(clauses=clauses)
    chunks = build_chunks(document, ChunkConfig("itemized"))
    assert len(chunks) == 2, "条款数必须与条目数一致，不能按内容 hash 误删条目"
    assert chunks[0].content_hash == chunks[1].content_hash
    assert chunks[0].chunk_id != chunks[1].chunk_id
    assert dedupe_chunks(chunks + chunks) == chunks


def test_chunk_store_replace_is_idempotent() -> None:
    document = make_document()
    chunks = build_chunks(document, ChunkConfig("structured"))

    from aipaas_pipeline.chunking import InMemoryChunkStore

    store = InMemoryChunkStore()
    assert store.replace_document(document.doc_id, chunks) == len(chunks)
    assert store.replace_document(document.doc_id, chunks) == len(chunks)
    assert store.count(document.doc_id) == len(chunks)


# --------------------------------------------------------------------------- 服务与预览


def test_service_persists_chunk_config_and_assertions(service) -> None:
    document = service.parse(
        md_jrt_code(), doc_id="doc-chunk-svc", filename="jrt.md",
        chunk_options=ChunkConfig("itemized"),
    )
    assert document.success
    assert document.extras["chunkStrategy"] == "itemized"
    assert document.extras["chunkCountMatchesClauses"] is True
    assert document.extras["chunkClauseCount"] == len(document.clauses)

    base = f"parse/default/doc-chunk-svc/v{document.version}"
    assert service.artifacts.exists(f"{base}/chunk-config.json")
    assert service.artifacts.exists(f"{base}/chunks.json")
    assert service.artifacts.exists("parse/default/doc-chunk-svc/latest-chunk.json")

    config = service.get_chunk_config("doc-chunk-svc")
    assert config is not None
    assert config["configVersion"] == document.extras["chunkConfigVersion"]
    assert config["assertions"]["countMatchesClauses"] is True

    artifact = service.get_chunks_artifact("doc-chunk-svc")
    assert artifact is not None
    assert artifact["chunkCount"] == len(document.clauses)
    assert all(chunk["type"] == "clause" for chunk in artifact["chunks"])

    record = service.get_record("doc-chunk-svc")
    assert record is not None
    assert record.extra["chunkConfigVersion"] == config["configVersion"]
    assert record.chunk_count == len(document.clauses)


def test_preview_matches_persisted_chunks(service) -> None:
    document = service.parse(md_jrt_code(), doc_id="doc-preview", filename="jrt.md")
    assert document.success
    preview = service.preview_chunks("doc-preview", ChunkConfig("structured"))
    assert preview is not None
    stored = service.chunks.list_chunks("doc-preview")
    assert [chunk["chunkId"] for chunk in preview["chunks"]] == [chunk["chunkId"] for chunk in stored]
    assert [chunk["hash"] for chunk in preview["chunks"]] == [chunk["hash"] for chunk in stored]
    assert preview["totalChunks"] == len(stored)
    assert preview["clauseChunks"] == len(document.clauses)
    assert service.list_versions("doc-preview") == [1], "预览不得写入新版本"

    truncated = service.preview_chunks("doc-preview", ChunkConfig("structured"), limit=1)
    assert truncated is not None
    assert truncated["truncated"] is True
    assert len(truncated["chunks"]) == 1
    assert truncated["totalChunks"] == len(stored)


def test_service_records_chunk_config_version_per_parse(service) -> None:
    first = service.parse(md_jrt_code(), doc_id="doc-cfg", filename="jrt.md",
                          chunk_options=ChunkConfig("fixed", size=64, overlap=8))
    first_version = first.extras["chunkConfigVersion"]
    second = service.parse(md_jrt_code(), doc_id="doc-cfg", filename="jrt.md",
                           chunk_options=ChunkConfig("fixed", size=128, overlap=8))
    assert second.extras["chunkConfigVersion"] != first_version
    latest = service.get_chunk_config("doc-cfg")
    assert latest is not None
    assert latest["configVersion"] == second.extras["chunkConfigVersion"]
    assert latest["config"]["size"] == 128


def _parse_sample() -> ParsedDocument:
    from aipaas_pipeline.context import ParseContext
    from aipaas_pipeline.registry import get_default_registry

    return get_default_registry().parse(
        "md", md_jrt_code(), ParseContext(doc_id="doc-sample", format="md")
    )


# --------------------------------------------------------------------------- 预览接口


def _client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ingest_worker.api.dependencies import get_parse_service
    from ingest_worker.api.routers.parse import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_parse_service] = lambda: service
    return TestClient(app)


def test_chunk_preview_api_matches_backend(service) -> None:
    client = _client(service)
    uploaded = client.post(
        "/api/v1/documents/api-chunk:parse",
        files={"file": ("jrt.md", md_jrt_code(), "text/markdown")},
        data={"chunkStrategy": "itemized"},
    )
    assert uploaded.status_code == 200
    payload = uploaded.json()["data"]
    assert payload["extras"]["chunkStrategy"] == "itemized"

    preview = client.post(
        "/api/v1/documents/api-chunk:chunk-preview",
        json={"strategy": "itemized"},
    )
    assert preview.status_code == 200
    data = preview.json()["data"]
    stored = client.get("/api/v1/documents/api-chunk/chunks").json()["data"]
    assert [chunk["chunkId"] for chunk in data["chunks"]] == [
        chunk["chunkId"] for chunk in stored["chunks"]
    ]
    assert data["totalChunks"] == stored["chunkCount"] == payload["extras"]["chunkCount"]
    assert data["invariants"]["countMatchesClauses"] is True

    config = client.get("/api/v1/documents/api-chunk/chunk-config").json()["data"]
    assert config["configVersion"] == stored["configVersion"]


def test_chunk_preview_api_rejects_invalid_params(service) -> None:
    client = _client(service)
    client.post(
        "/api/v1/documents/api-bad:parse",
        files={"file": ("jrt.md", md_jrt_code(), "text/markdown")},
    )
    invalid = client.post(
        "/api/v1/documents/api-bad:chunk-preview",
        json={"strategy": "fixed", "size": 10},
    )
    assert invalid.status_code == 400
    assert "分块参数不合法" in invalid.json()["message"]

    missing = client.post("/api/v1/documents/not-exists:chunk-preview", json={})
    assert missing.status_code == 404
