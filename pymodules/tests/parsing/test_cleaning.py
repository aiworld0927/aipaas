"""#14 数据清洗：规则单测、保护、差异追踪与样本回归。"""
from __future__ import annotations

from pathlib import Path

import pytest

from aipaas_pipeline.chunking import InMemoryChunkStore
from aipaas_pipeline.cleaning import RULES_VERSION, CleaningOptions, clean_document
from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.models import Clause, ParsedDocument, SectionNode, TableBlock
from aipaas_pipeline.registry import ParserRegistry
from aipaas_pipeline.repository import InMemoryParseResultRepository
from aipaas_pipeline.samples import SAMPLES, write_samples
from aipaas_pipeline.service import ParseService
from aipaas_pipeline.storage import InMemoryArtifactStore

DIRTY_TEXT = "  组织应  \x00建立\ufeff分级制度â€™  "


def make_document(text: str = DIRTY_TEXT) -> ParsedDocument:
    section = SectionNode("s4", " 第4章   数据安全 ", 2, clause_no="4")
    clause = Clause("4.1.1", " 数据分级 ", text, " 第4章 / 4.1 ", 3, page_no=1, section_id="s4")
    table = TableBlock(
        "tbl_1", 1,
        [[" 条款 ", " 要求 "], [" 4.1.1 ", " =SUM(A1:A2) "]],
        caption=" 表1  示例 ",
    )
    return ParsedDocument(
        doc_id="doc-clean",
        parser="test",
        success=True,
        title=" 标准  标题 ",
        standard_no="GB/T 1-2020",
        chapter_tree=[section],
        clauses=[clause],
        tables=[table],
        raw_text="  第一行  \r\n\r\n\r\n\r\n第二行\x00  ",
        pages=1,
        extras={"codeBlocks": [{"language": "python", "content": "def  f():\n    return  1"}]},
    )


def test_cleans_illegal_chars_blank_lines_and_duplicate_spaces() -> None:
    result = clean_document(make_document())
    cleaned = result.document

    clause = cleaned.clauses[0]
    assert clause.clause_no == "4.1.1"
    assert "\x00" not in clause.text and "\ufeff" not in clause.text and "â€™" not in clause.text
    assert "组织应" in clause.text and "建立" in clause.text and "分级制度" in clause.text
    assert "  " not in clause.text
    assert cleaned.title == "标准 标题"
    assert cleaned.standard_no == "GB/T 1-2020"
    assert cleaned.raw_text == "第一行\n\n第二行"

    summary = result.diff["summary"]
    assert summary["rulesVersion"] == RULES_VERSION
    assert summary["changedFields"] > 0
    assert summary["removedChars"] > 0
    assert summary["removedBlankLines"] == 2


def test_clause_numbers_and_section_tree_are_never_rewritten() -> None:
    result = clean_document(make_document())
    invariants = result.diff["invariants"]

    assert invariants["clauseNumbersPreserved"] is True
    assert invariants["sectionTreePreserved"] is True
    assert invariants["tableShapePreserved"] is True
    assert [clause.clause_no for clause in result.document.clauses] == ["4.1.1"]
    assert result.document.chapter_tree[0].section_id == "s4"
    assert result.document.chapter_tree[0].level == 2


def test_tables_and_formulas_are_protected() -> None:
    result = clean_document(make_document())
    table = result.document.tables[0]

    assert table.rows[0] == ["条款", "要求"]
    assert table.rows[1][0] == "4.1.1"
    assert table.rows[1][1] == " =SUM(A1:A2) "
    assert table.caption == "表1 示例"
    assert result.diff["summary"]["protectedTableCells"] == 1


def test_code_blocks_are_protected_in_extras_and_raw_text() -> None:
    document = make_document()
    document.raw_text = "说明\n```python\ndef  f( ):\n    return  1\n```\n正文 结束"

    result = clean_document(document)

    assert result.document.extras["codeBlocks"][0]["content"] == "def  f():\n    return  1"
    assert "def  f( ):\n    return  1" in result.document.raw_text
    assert "正文 结束" in result.document.raw_text
    assert result.diff["invariants"]["codeBlocksPreserved"] is True
    assert result.diff["summary"]["protectedCodeBlocks"] == 1


def test_effective_content_is_not_deleted() -> None:
    text = "4.1.1 组织应建立数据分级制度，每年至少评估 1 次，留存记录不少于 3 年。"
    result = clean_document(make_document(text=text))

    for token in ("组织应建立数据分级制度", "每年至少评估", "1", "3", "留存记录"):
        assert token in result.document.clauses[0].text
    assert result.document.clauses[0].clause_no == "4.1.1"
    assert result.document.clauses[0].text.strip() != ""


def test_diff_entries_capture_before_after_and_honor_limit() -> None:
    document = make_document()
    document.clauses = [
        Clause(f"4.1.{index}", " 标题 ", f"  正文  {index}  内容  ", " 第4章 ", 3, page_no=1)
        for index in range(1, 6)
    ]
    result = clean_document(document, CleaningOptions(max_diff_entries=6))
    diff = result.diff

    assert diff["summary"]["changedFields"] > 6
    assert diff["summary"]["diffEntries"] == 6
    assert diff["summary"]["diffTruncated"] is True
    for change in diff["changes"]:
        assert change["before"] != change["after"]
        assert change["rules"]
    assert any(change["path"].startswith("clauses[") for change in diff["changes"])


def test_disabled_cleaning_keeps_original_text() -> None:
    document = make_document()
    result = clean_document(document, CleaningOptions(enabled=False))

    assert result.document.clauses[0].text == DIRTY_TEXT
    assert result.diff["summary"]["cleaningApplied"] is False
    assert result.document.extras["cleaningApplied"] is False


class _DirtyParser:
    name = "dirty"
    formats = ("dirty",)

    def parse(self, data: bytes, ctx: ParseContext) -> ParsedDocument:
        document = make_document()
        document.doc_id = ctx.doc_id
        document.tenant_id = ctx.tenant_id
        return document


def test_service_writes_clean_artifacts_without_overwriting_parse_result() -> None:
    registry = ParserRegistry()
    registry.register(_DirtyParser())
    artifacts = InMemoryArtifactStore()
    chunks = InMemoryChunkStore()
    service = ParseService(registry, artifacts, chunks, InMemoryParseResultRepository())

    service.parse(b"raw", doc_id="doc-9", filename="a.dirty", fmt="dirty")
    record = service.get_record("doc-9")
    assert record is not None

    original = artifacts.get_bytes(record.artifact_key)
    assert original is not None and b"\\u0000" in original

    clean = service.get_clean_result("doc-9")
    assert clean is not None
    assert clean["version"] == record.version
    assert "\x00" not in clean["clauses"][0]["text"]
    assert clean["extras"]["cleaning"]["rulesVersion"] == RULES_VERSION

    diff = service.get_clean_diff("doc-9")
    assert diff is not None
    assert diff["summary"]["changedFields"] >= 3
    assert diff["invariants"]["clauseNumbersPreserved"] is True

    chunk_list = chunks.list_chunks("doc-9")
    assert chunk_list
    assert all("\x00" not in str(chunk["content"]) for chunk in chunk_list)
    assert any("组织应 建立分级制度" in str(chunk["content"]) for chunk in chunk_list)


@pytest.mark.parametrize("relative", [name for name in SAMPLES if "scanned" not in name and "garbled" not in name])
def test_sample_regression_preserves_structure(tmp_path: Path, relative: str) -> None:
    files = write_samples(tmp_path)
    service = ParseService(
        artifacts=InMemoryArtifactStore(),
        chunks=InMemoryChunkStore(),
        results=InMemoryParseResultRepository(),
    )
    document = service.parse(
        files[relative].read_bytes(),
        doc_id="reg-" + relative.replace("/", "-"),
        filename=files[relative].name,
        fmt=SAMPLES[relative][0],
    )
    assert document.success is True

    result = clean_document(document)
    invariants = result.diff["invariants"]
    assert invariants["clauseNumbersPreserved"] is True
    assert invariants["sectionTreePreserved"] is True
    assert invariants["tableShapePreserved"] is True
    assert invariants["codeBlocksPreserved"] is True
    assert len(result.document.clauses) == len(document.clauses) > 0
    assert result.document.raw_text.strip()
