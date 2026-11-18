"""#106 引用/幻觉抑制与 #108 拒答策略测试。"""

from __future__ import annotations

from aipaas_generation import (
    GroundingChecker,
    RefusalMessages,
    RefusalPolicy,
    RefusalStats,
    estimate_confidence,
)
from aipaas_generation.refusal import (
    REASON_LOW_CONFIDENCE,
    REASON_NO_CITATION,
    REASON_NO_EVIDENCE,
)


def test_citations_resolve_to_chunk_doc_and_clause(clauses):
    answer = "金融机构应当建立风险评估机制。[1] 存储个人金融信息应当加密。[3]"
    result = GroundingChecker().check(answer, clauses)

    assert [citation.chunk_id for citation in result.citations] == ["chunk-1", "chunk-3"]
    assert result.citations[0].document_id == "doc-1"
    assert result.citations[0].clause_no == "4.1.1"
    assert result.citations[1].clause_no == "5.2.1"
    assert result.unsupported_claims == []


def test_unknown_marker_removed_and_annotated(clauses):
    result = GroundingChecker().check("依据 [9]，金融机构必须每日审计。", clauses)
    assert result.unknown_markers == [9]
    assert "[9]" not in result.content
    assert result.annotated_claims == 1
    assert "未检索到直接依据" in result.content
    assert any("CITATION_UNKNOWN_MARKER" in warning for warning in result.warnings)


def test_unsupported_normative_claim_annotated(clauses):
    result = GroundingChecker().check("金融机构必须每日审计。", clauses)
    assert result.annotated_claims == 1
    assert len(result.unsupported_claims) == 1
    assert result.content.endswith("（未检索到直接依据，建议查阅原标准）")
    assert result.citations == []


def test_drop_mode_removes_unsupported_claim(clauses):
    result = GroundingChecker(mode="drop").check("金融机构必须每日审计。", clauses)
    assert result.dropped_claims == 1
    assert "每日审计" not in result.content


def test_descriptive_text_without_citation_is_kept(clauses):
    result = GroundingChecker().check("我可以帮你查询金融标准。", clauses)
    assert result.content == "我可以帮你查询金融标准。"
    assert result.citations == []
    assert any("NO_CITATION" in warning for warning in result.warnings)


def test_explicit_clause_number_creates_citation(clauses):
    result = GroundingChecker().check("依据 4.1.1 的要求，应建立评估机制。", clauses)
    assert [citation.chunk_id for citation in result.citations] == ["chunk-1"]


def test_confidence_estimation(clauses):
    assert estimate_confidence(clauses, []) == 0.0
    assert estimate_confidence(clauses, ["not-exists"]) == 0.0
    score = estimate_confidence(clauses, ["chunk-1"])
    assert 0.75 <= score <= 0.85


def test_refusal_for_no_evidence_and_low_confidence():
    policy = RefusalPolicy(confidence_threshold=0.35)
    no_evidence = policy.decide(has_clauses=False, citation_count=0, confidence=0.0)
    assert no_evidence.refused is True
    assert no_evidence.reason_code == REASON_NO_EVIDENCE
    assert "查阅" in (no_evidence.message or "")

    low = policy.decide(has_clauses=True, citation_count=1, confidence=0.2)
    assert low.reason_code == REASON_LOW_CONFIDENCE
    assert "0.20" in (low.reason_text or "")

    no_citation = policy.decide(has_clauses=True, citation_count=0, confidence=0.9)
    assert no_citation.reason_code == REASON_NO_CITATION

    passed = policy.decide(has_clauses=True, citation_count=1, confidence=0.9)
    assert passed.refused is False


def test_refusal_messages_and_threshold_configurable():
    messages = RefusalMessages(no_evidence="自定义：没有可用条款。", guidance="请联系标准管理部门。")
    policy = RefusalPolicy(confidence_threshold=0.99, messages=messages)
    decision = policy.decide(has_clauses=False, citation_count=0, confidence=0.0)
    assert (decision.message or "").startswith("自定义：没有可用条款。")
    assert "请联系标准管理部门。" in (decision.message or "")

    strict = policy.decide(has_clauses=True, citation_count=1, confidence=0.98)
    assert strict.refused is True
    assert strict.reason_code == REASON_LOW_CONFIDENCE


def test_refusal_statistics_by_reason_and_tenant():
    stats = RefusalStats()
    stats.record_refusal(REASON_NO_EVIDENCE, tenant_id="t1")
    stats.record_refusal(REASON_LOW_CONFIDENCE, tenant_id="t1")
    stats.record_answer()
    snapshot = stats.snapshot()
    assert snapshot["total"] == 3
    assert snapshot["refused"] == 2
    assert snapshot["refusalRate"] == round(2 / 3, 4)
    assert snapshot["byReason"][REASON_NO_EVIDENCE] == 1
    assert snapshot["byTenant"]["t1"] == 2
