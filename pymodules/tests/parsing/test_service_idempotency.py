from __future__ import annotations

from aipaas_pipeline.samples import pdf_gbt_35273


def test_reparse_versions_increment_without_duplicate_chunks(service) -> None:
    first = service.parse(pdf_gbt_35273(), doc_id="doc-idem", filename="gb.pdf")
    assert first.success
    first_keys = [chunk["chunkId"] for chunk in service.chunks.list_chunks("doc-idem")]
    first_count = service.chunk_count("doc-idem")
    assert first_count > 0

    second = service.parse(pdf_gbt_35273(), doc_id="doc-idem", filename="gb.pdf")
    assert second.success
    second_keys = [chunk["chunkId"] for chunk in service.chunks.list_chunks("doc-idem")]
    assert second_keys == first_keys, "重试不得产生重复 chunk 或改变分块键"
    assert service.chunk_count("doc-idem") == first_count
    assert first.extras["chunkIdsDigest"] == second.extras["chunkIdsDigest"]
    assert service.list_versions("doc-idem") == [1, 2]


def test_artifacts_are_versioned_and_manifest_recorded(service) -> None:
    document = service.parse(pdf_gbt_35273(), doc_id="doc-art", filename="gb.pdf")
    version = document.version
    base = f"parse/default/doc-art/v{version}"
    assert service.artifacts.exists(f"{base}/parse-result.json")
    assert service.artifacts.exists(f"{base}/manifest.json")
    assert service.artifacts.exists("parse/default/doc-art/latest.json")
    latest = service.get_result("doc-art")
    assert latest is not None
    assert latest["version"] == version
    assert latest["documentId"] == "doc-art"
    assert len(latest["clauses"]) == len(document.clauses)
    assert service.get_record("doc-art").artifact_key.endswith("parse-result.json")


def test_failed_reparse_keeps_previous_chunks_and_records_failure(service) -> None:
    good = service.parse(pdf_gbt_35273(), doc_id="doc-fail", filename="gb.pdf")
    count_before = service.chunk_count("doc-fail")
    assert good.success

    failed = service.parse(b"%PDF-1.7 broken", doc_id="doc-fail", filename="gb.pdf")
    assert not failed.success
    assert failed.failure_code
    assert failed.retry_count == 2, "失败自动重试不超过 2 次"
    assert service.chunk_count("doc-fail") == count_before, "失败不得破坏已入库 chunk"
    record = service.get_record("doc-fail")
    assert record is not None and record.success is False
    assert record.failure_code == failed.failure_code
    assert service.list_versions("doc-fail") == [1, 2]


def test_unexpected_parser_exception_is_not_swallowed(service) -> None:
    class _CrashParser:
        name = "crash"
        formats = ("crash",)

        def parse(self, data, ctx):
            raise RuntimeError("boom")

    from aipaas_pipeline.parsers.pdf_parser import PdfParser
    from aipaas_pipeline.registry import ParserRegistry

    registry = ParserRegistry()
    registry.register(PdfParser())
    registry.register(_CrashParser())
    service.registry = registry
    document = service.parse(b"x", doc_id="doc-crash", filename="a.crash")
    assert not document.success
    assert document.failure_code == "PARSE.INTERNAL"
    assert document.retry_count == 2
    assert any("boom" in warning.message for warning in document.warnings)
