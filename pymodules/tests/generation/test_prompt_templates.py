"""#103 Prompt 组装测试：三段结构、窗口截断、版本可追溯。"""

from __future__ import annotations

import pytest

from aipaas_generation import (
    ContextWindowPolicy,
    PromptTemplate,
    PromptTemplateRegistry,
    RetrievedClause,
    TemplateNotFound,
    assemble_prompt,
)


def test_assembly_contains_system_context_and_question(clauses):
    registry = PromptTemplateRegistry.with_builtin_defaults()
    template = registry.get("rag-answer")
    assembly = assemble_prompt(template, clauses, "金融机构需要多久评估一次风险？")

    assert assembly.template_id == "rag-answer@1"
    assert "只能依据【检索条款】作答" in assembly.system_prompt
    assert "[1]" in assembly.user_prompt and "4.1.1" in assembly.user_prompt
    assert "【用户问题】" in assembly.user_prompt
    assert "金融机构需要多久评估一次风险？" in assembly.user_prompt
    assert assembly.used_clause_ids == ["chunk-1", "chunk-2", "chunk-3"]
    assert assembly.warnings == []


def test_overlong_context_truncated_without_error():
    long_clauses = [
        RetrievedClause(
            chunk_id=f"chunk-{index}",
            document_id=f"doc-{index}",
            clause_no=f"4.1.{index}",
            standard_no="JR/T 0088",
            text="金融机构应当建立风险评估机制。" * 60,
            score=0.9 - index * 0.01,
        )
        for index in range(1, 8)
    ]
    template = PromptTemplateRegistry.with_builtin_defaults().get("rag-answer")
    assembly = assemble_prompt(
        template,
        long_clauses,
        "风险评估要求？",
        policy=ContextWindowPolicy(max_context_chars=800, min_clause_chars=100),
    )
    assert assembly.truncated is True
    assert assembly.warnings
    assert 1 <= len(assembly.used_clause_ids) < len(long_clauses)
    assert assembly.dropped_clause_ids
    # 组装成功且上下文不超预算（条目间的空行除外）
    assert assembly.context_chars <= 800 + 20


def test_per_clause_compression():
    clauses = [
        RetrievedClause(
            chunk_id="c1", document_id="d1", text="甲" * 500, clause_no="1.1", standard_no="S"
        )
    ]
    template = PromptTemplateRegistry.with_builtin_defaults().get("rag-answer")
    assembly = assemble_prompt(
        template,
        clauses,
        "问题",
        policy=ContextWindowPolicy(per_clause_chars=80, max_context_chars=600),
    )
    assert assembly.truncated is True
    assert "甲" * 80 in assembly.user_prompt
    assert "…" in assembly.user_prompt


def test_extremely_small_window_still_builds_prompt(clauses):
    template = PromptTemplateRegistry.with_builtin_defaults().get("rag-answer")
    assembly = assemble_prompt(
        template, clauses, "问题", policy=ContextWindowPolicy(max_context_chars=10)
    )
    assert assembly.prompt
    assert len(assembly.used_clause_ids) == 1
    assert assembly.truncated is True


def test_template_versions_are_traceable():
    registry = PromptTemplateRegistry()
    registry.register(
        PromptTemplate(
            name="rag-answer",
            version="1",
            system_instruction="只依据检索条款作答。",
            user_template="{context}\n{question}",
        )
    )
    registry.register(
        PromptTemplate(
            name="rag-answer",
            version="2",
            system_instruction="只依据检索条款作答，并标注条款编号。",
            user_template="{context}\n{question}",
        )
    )
    latest = registry.get("rag-answer")
    assert latest.version == "2"
    assert registry.get("rag-answer", "1").version == "1"

    versions = registry.list_versions("rag-answer")
    assert [item["version"] for item in versions] == ["1", "2"]
    assert versions[1]["active"] is True
    assert versions[0]["contentHash"] != versions[1]["contentHash"]


def test_duplicate_version_and_unknown_template():
    registry = PromptTemplateRegistry.with_builtin_defaults()
    with pytest.raises(ValueError):
        registry.register(
            PromptTemplate(
                name="rag-answer", version="1", system_instruction="x", user_template="{context}"
            )
        )
    with pytest.raises(TemplateNotFound):
        registry.get("missing")
    with pytest.raises(TemplateNotFound):
        registry.get("rag-answer", "99")


def test_describe_has_no_prompt_content(clauses):
    template = PromptTemplateRegistry.with_builtin_defaults().get("rag-answer")
    assembly = assemble_prompt(template, clauses, "问题")
    describe = str(assembly.describe())
    assert "金融机构应当建立个人金融信息风险评估机制" not in describe
    assert "promptChars" in describe
    assert assembly.fingerprint() in describe
