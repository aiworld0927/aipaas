from __future__ import annotations

from aipaas_pipeline.chunking import InMemoryChunkStore
from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.parsers.markdown_parser import MarkdownParser
from aipaas_pipeline.samples import md_gbt, md_jrt_code, md_nested


def _parse(data: bytes, service=None):
    return MarkdownParser().parse(data, ParseContext(doc_id="md", format="md"))


def test_markdown_headings_clauses_tables_lists() -> None:
    document = _parse(md_gbt())
    assert document.success
    assert document.standard_no == "GB/T 35273-2020"
    assert "个人信息安全规范" in document.title
    assert {clause.clause_no for clause in document.clauses} >= {"4", "4.1", "4.1.1", "4.1.2"}
    assert document.tables and len(document.tables[0].rows) == 3
    assert len(document.extras["lists"]) == 2
    assert document.chapter_tree[0].title.startswith("GB/T 35273-2020")


def test_code_block_is_kept_intact_and_not_mixed_into_clause() -> None:
    document = _parse(md_jrt_code())
    assert document.success
    blocks = document.extras["codeBlocks"]
    assert blocks and blocks[0]["language"] == "yaml"
    assert "dataAsset:" in blocks[0]["content"]
    clause = next(item for item in document.clauses if item.clause_no == "4.1.1")
    assert "dataAsset" not in clause.text
    assert "金融机构应识别数据资产" in clause.text
    assert any(warning.code == "PARSE.CODE_BLOCK_ATOMIC" for warning in document.warnings)
    assert "```yaml" in document.raw_text


def test_nested_markdown_lists_and_multiple_code_blocks() -> None:
    document = _parse(md_nested())
    assert {clause.clause_no for clause in document.clauses} >= {"4", "4.1", "4.1.1", "4.1.2", "4.1.2.1"}
    assert len(document.extras["codeBlocks"]) == 2
    lists = document.extras["lists"]
    assert {item["level"] for item in lists} >= {0, 1}


def test_markdown_chunks_keep_code_block_atomic() -> None:
    from aipaas_pipeline.chunking import build_chunks

    document = _parse(md_jrt_code())
    chunks = build_chunks(document)
    code_chunks = [chunk for chunk in chunks if chunk.chunk_type == "code"]
    assert len(code_chunks) == 1
    assert code_chunks[0].content == document.extras["codeBlocks"][0]["content"]
    clause_chunks = [chunk for chunk in chunks if chunk.chunk_type == "clause"]
    assert all("dataAsset:" not in chunk.content for chunk in clause_chunks)
