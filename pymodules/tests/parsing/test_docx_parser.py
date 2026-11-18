from __future__ import annotations

from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.options import ParseOptions
from aipaas_pipeline.parsers.docx_parser import DocxParser
from aipaas_pipeline.samples import docx_gbt_22239, docx_jrt_0197_revised, docx_nested


def test_docx_headings_clauses_tables_and_lists() -> None:
    document = DocxParser().parse(
        docx_gbt_22239(), ParseContext(doc_id="docx1", format="docx")
    )
    assert document.success
    assert document.standard_no == "GB/T 22239-2019"
    assert "网络安全等级保护基本要求" in document.title
    clauses = {clause.clause_no: clause for clause in document.clauses}
    assert {"4", "4.1", "4.1.1", "4.1.2"} <= set(clauses)
    assert "鉴别信息应具有复杂度要求" in clauses["4.1.1"].text
    level_of: dict[str, int] = {}

    def flatten(nodes):
        for node in nodes:
            if node.clause_no:
                level_of[node.clause_no] = node.level
            flatten(node.children)

    flatten(document.chapter_tree)
    assert level_of["4"] == 1
    assert level_of["4.1.1"] == 3
    assert document.tables and document.tables[0].rows[1][0] == "身份鉴别"
    assert document.extras["lists"], "应保留列表项"


def test_docx_revision_ignore_and_keep() -> None:
    parser = DocxParser()
    ignored = parser.parse(
        docx_jrt_0197_revised(),
        ParseContext(doc_id="docx2", format="docx", options=ParseOptions(revision_mode="ignore")),
    )
    assert "新增：应每年更新数据资产目录。" in ignored.raw_text
    assert "⟦插入" not in ignored.raw_text
    assert "⟦删除" not in ignored.raw_text
    assert "删除：每季度更新" not in ignored.raw_text
    assert ignored.extras["revisionCount"] == 2

    kept = parser.parse(
        docx_jrt_0197_revised(),
        ParseContext(doc_id="docx3", format="docx",
                     options=ParseOptions(revision_mode="keep", comment_mode="keep")),
    )
    assert "⟦插入:新增：应每年更新数据资产目录。⟧" in kept.raw_text
    assert "⟦删除:删除：每季度更新。⟧" in kept.raw_text


def test_docx_comments_ignore_or_keep() -> None:
    parser = DocxParser()
    ignored = parser.parse(
        docx_jrt_0197_revised(),
        ParseContext(doc_id="docx4", format="docx", options=ParseOptions(comment_mode="ignore")),
    )
    assert ignored.extras["commentCount"] == 1
    assert "请补充数据资产目录模板" not in ignored.raw_text
    assert any(warning.code == "PARSE.COMMENTS_IGNORED" for warning in ignored.warnings)

    kept = parser.parse(
        docx_jrt_0197_revised(),
        ParseContext(doc_id="docx5", format="docx", options=ParseOptions(comment_mode="keep")),
    )
    assert "⟦批注:张三:请补充数据资产目录模板。⟧" in kept.raw_text


def test_nested_docx_levels_and_numbered_lists() -> None:
    document = DocxParser().parse(docx_nested(), ParseContext(doc_id="docx6", format="docx"))
    numbers = {clause.clause_no for clause in document.clauses}
    assert {"4", "4.1", "4.1.1", "4.1.2"} <= numbers
    lists = document.extras["lists"]
    assert len(lists) == 2
    assert any(item["level"] == 1 for item in lists)
    assert document.tables and len(document.tables[0].rows) == 3
