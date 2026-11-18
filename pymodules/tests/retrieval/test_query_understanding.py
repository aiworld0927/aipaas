"""#63 查询归一：编号归一、清洗分词、空/超长/特殊字符兜底与耗时。"""

from __future__ import annotations

from aipaas_retrieval import normalize_clause_no, normalize_standard_no, understand_query


def test_standard_no_normalization_variants() -> None:
    assert normalize_standard_no("jrt 0088") == "JR/T 0088"
    assert normalize_standard_no("JR/T0088-2016") == "JR/T 0088-2016"
    assert normalize_standard_no("GB/T35273—2020") == "GB/T 35273-2020"
    assert normalize_standard_no("ISO/IEC 27001:2013") == "ISO/IEC 27001-2013"
    assert normalize_standard_no("没有编号") is None


def test_clause_no_normalization_variants() -> None:
    assert normalize_clause_no("第4.1.1条") == "4.1.1"
    assert normalize_clause_no("４.１.１") == "4.1.1"
    assert normalize_clause_no("见 4.1.1 条款") == "4.1.1"
    assert normalize_clause_no("2020-11-18") is None


def test_query_normalized_with_both_anchors() -> None:
    result = understand_query("jrt 0088-2016 第4.1.1条 风险评估")
    assert result.standard_no == "JR/T 0088-2016"
    assert result.clause_no == "4.1.1"
    assert result.normalized_query == "JR/T 0088-2016 4.1.1 风险评估"
    assert result.tokens == ("风险", "险评", "评估")
    assert result.warnings == ()
    assert result.latency_ms >= 0


def test_empty_query_fallback() -> None:
    for raw in (None, "", "   ", "\u3000\u3000"):
        result = understand_query(raw)
        assert result.normalized_query == ""
        assert result.tokens == ()
        assert any(item.startswith("QUERY_EMPTY") for item in result.warnings)


def test_overlong_query_is_truncated_with_warning() -> None:
    result = understand_query("风险评估" * 1000)
    assert result.truncated is True
    assert len(result.normalized_query) <= 2000
    assert any(item.startswith("QUERY_TRUNCATED") for item in result.warnings)


def test_special_characters_are_cleaned() -> None:
    result = understand_query("\x00\u200b风险评估\ufeff")
    assert result.normalized_query == "风险评估"
    assert any(item.startswith("QUERY_SPECIAL_CHARS_REMOVED") for item in result.warnings)


def test_special_only_query_does_not_raise() -> None:
    result = understand_query("!!!??? ---")
    assert result.standard_no is None
    assert result.clause_no is None
    assert result.tokens == ()
    assert any(item.startswith("QUERY_NO_EFFECTIVE_TOKEN") for item in result.warnings)


def test_tokens_are_deduplicated_and_capped() -> None:
    result = understand_query("风险评估 " * 200)
    assert len(result.tokens) <= 64
    assert len(result.tokens) == len(set(result.tokens))


def test_pure_anchor_query_has_no_token_warning() -> None:
    result = understand_query("JR/T 0088")
    assert result.standard_no == "JR/T 0088"
    assert result.tokens == ()
    assert not any(item.startswith("QUERY_NO_EFFECTIVE_TOKEN") for item in result.warnings)
