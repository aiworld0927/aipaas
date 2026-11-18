"""#110 Markdown 格式化测试：结构保留、超长分段、信息不增删。"""

from __future__ import annotations

from aipaas_generation import Citation, MarkdownFormatter, plain_information, render_citations

STRUCTURED = """## 结论

金融机构应当建立风险评估机制。

- 每年至少评估一次
- 评估结果留档

> 依据 JR/T 0088 第 4.1.1 条

| 项目 | 要求 |
| --- | --- |
| 频率 | 每年 |
| 留档 | 是 |
"""


def test_markdown_structures_preserved_and_no_information_change():
    formatter = MarkdownFormatter(max_segment_chars=5000)
    result = formatter.format(STRUCTURED)
    assert result.segment_count == 5  # 段落数（未触发超长切分）
    assert result.warnings == []
    assert "## 结论" in result.content
    assert "- 每年至少评估一次" in result.content
    assert "> 依据 JR/T 0088 第 4.1.1 条" in result.content
    assert "| 项目 | 要求 |" in result.content
    assert plain_information(result.content) == plain_information(STRUCTURED)


def test_long_answer_segmented_without_information_loss():
    sentence = "金融机构应当每年至少开展一次个人金融信息风险评估，并留存评估记录。"
    original = "".join(sentence for _ in range(80))
    formatter = MarkdownFormatter(max_segment_chars=300)
    result = formatter.format(original)

    assert result.segment_count > 1
    assert all(len(segment) <= 300 for segment in result.segments)
    assert plain_information(result.content) == plain_information(original)
    assert any("ANSWER_SEGMENTED" in warning for warning in result.warnings)


def test_single_very_long_sentence_hard_split():
    original = "甲" * 1250
    result = MarkdownFormatter(max_segment_chars=300).format(original)
    assert result.segment_count == 5
    assert plain_information(result.content) == plain_information(original)


def test_citation_appendix_is_optional():
    citations = [
        Citation(
            chunk_id="c1",
            document_id="d1",
            clause_no="4.1.1",
            standard_no="JR/T 0088",
        )
    ]
    formatter = MarkdownFormatter()
    default = formatter.format("答案正文。", citations=citations)
    assert "引用条款" not in default.content
    assert plain_information(default.content) == plain_information("答案正文。")

    appended = formatter.format("答案正文。", citations=citations, append_citations=True)
    assert "引用条款" in appended.content
    assert "[1] JR/T 0088 4.1.1" in appended.content
    assert render_citations(citations) in appended.content
