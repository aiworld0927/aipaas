"""P0 质量门禁复算：Recall / Hit / Faithfulness / Citation / 幻觉率 / 拒答。

用法：
    cd pymodules && uv run python scripts/acceptance/golden_eval.py

依赖说明：生成层使用确定性抽取式桩替代真实 LLM（后置期依赖），
检索层复用生产 ``aipaas_retrieval``；指标可完全离线复现。
"""

from __future__ import annotations

import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aipaas_generation as G
import aipaas_retrieval as R
from harness import (  # noqa: E402
    ACCEPTANCE_DIR,
    GOLDEN_DIR,
    ExtractiveStubLLM,
    build_encoder,
    build_generation_service,
    build_retrieval_service,
    build_searcher,
    load_jsonl,
    write_json,
    write_jsonl,
)

TOP_K = 8
THRESHOLDS = {
    "recall": 0.85,
    "hit": 0.90,
    "faithfulness": 0.90,
    "citation": 0.90,
    "hallucination": 0.05,
    "refusal": 1.00,
    "false_refusal": 0.05,
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def answer_lines(content: str) -> list[str]:
    lines = []
    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def strip_marker(line: str) -> str:
    line = re.sub(r"^[-*]\s*", "", line.strip())
    line = re.sub(r"\s*\[\d+\]\s*$", "", line)
    return line.strip()


def evaluate(items, corpus_map, retrieval, generation, access, top_k: int):
    details = []
    answerable = [item for item in items if item["answerable"]]
    no_answer = [item for item in items if not item["answerable"]]
    hits = recalls = 0.0
    cited = 0
    answered = 0
    refused_answerable = 0
    refused_no_answer = 0
    support_sentences = 0
    unsupported_sentences = 0
    latencies: list[float] = []

    for item in items:
        filters = item.get("filters") or {}
        retrieval_filters = R.RetrievalFilters(
            standard_no=filters.get("standard_no"),
            clause_no=filters.get("clause_no"),
        )
        request = R.RetrieveRequest(
            query=item["query"],
            kb_ids=item["kb_ids"],
            top_k=top_k,
            retrieval_mode="vector",
            rerank=False,
            filters=retrieval_filters,
            trace_id=item["id"],
        )
        result = retrieval.retrieve(request, access)
        retrieved_ids = [hit.chunk_id for hit in result.hits]
        expected = item.get("expected_clause_ids") or []
        hit = bool(set(retrieved_ids) & set(expected))
        recall = (
            len(set(retrieved_ids) & set(expected)) / len(expected)
            if expected
            else 0.0
        )

        answer = generation.answer(
            G.AnswerRequest(
                query=item["query"],
                kb_ids=item["kb_ids"],
                top_k=top_k,
                filters=retrieval_filters,
                trace_id=item["id"],
            ),
            access,
        )
        latencies.append(result.latency_ms + float(answer.timings.get("totalMs", 0)))

        cited_ids = [citation.chunk_id for citation in answer.citations]
        citation_hit = bool(set(cited_ids) & set(expected)) if expected else False
        citation_fields_ok = all(
            citation.clause_no and citation.standard_no for citation in answer.citations
        ) and (bool(answer.citations) or answer.refused)
        item_unsupported = 0
        item_sentences = 0
        if not answer.refused:
            answered += 1
            context_norms = [
                normalize(corpus_map[cid]["text"])
                for cid in cited_ids
                if cid in corpus_map
            ]
            for line in answer_lines(answer.content):
                claim = strip_marker(line)
                if not claim:
                    continue
                item_sentences += 1
                if any(normalize(claim) in context for context in context_norms):
                    support_sentences += 1
                else:
                    unsupported_sentences += 1
                    item_unsupported += 1
        if item["answerable"]:
            hits += 1.0 if hit else 0.0
            recalls += recall
            cited += 1 if (citation_hit and citation_fields_ok) else 0
            if answer.refused:
                refused_answerable += 1
        elif answer.refused:
            refused_no_answer += 1

        details.append(
            {
                "id": item["id"],
                "type": item["type"],
                "query": item["query"],
                "answerable": item["answerable"],
                "expectedClauseIds": expected,
                "retrievedClauseIds": retrieved_ids,
                "hit": hit,
                "recall": round(recall, 4),
                "refused": answer.refused,
                "refusalReason": answer.refusal_reason,
                "citedClauseIds": cited_ids,
                "citationHit": citation_hit,
                "citationFieldsOk": citation_fields_ok,
                "answer": answer.content,
                "unsupportedSentences": item_unsupported,
                "sentences": item_sentences,
                "retrievalLatencyMs": result.latency_ms,
                "generationLatencyMs": answer.timings.get("totalMs"),
            }
        )

    total_sentences = support_sentences + unsupported_sentences
    metrics = {
        "topK": top_k,
        "answerable": len(answerable),
        "noAnswer": len(no_answer),
        "recall": round(recalls / max(1, len(answerable)), 4),
        "hit": round(hits / max(1, len(answerable)), 4),
        "faithfulness": round(support_sentences / max(1, total_sentences), 4),
        "citation": round(cited / max(1, len(answerable)), 4),
        "hallucination": round(unsupported_sentences / max(1, total_sentences), 4),
        "refusalAccuracy": round(refused_no_answer / max(1, len(no_answer)), 4),
        "falseRefusalRate": round(refused_answerable / max(1, len(answerable)), 4),
        "answered": answered,
        "p50LatencyMs": round(statistics.median(latencies or [0.0]), 2),
        "p95LatencyMs": round(
            sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0.0,
            2,
        ),
    }
    return metrics, details


def evaluate_with_encoder(items, corpus, encoder, access, top_k):
    searcher = build_searcher(corpus, encoder)
    retrieval = build_retrieval_service(searcher, encoder)
    generation = build_generation_service(
        retrieval, llm=ExtractiveStubLLM(idf=getattr(encoder, "idf", None))
    )
    return evaluate(items, {row["chunk_id"]: row for row in corpus}, retrieval, generation, access, top_k)


def gate(metrics: dict) -> tuple[bool, dict[str, bool]]:
    checks = {
        "Recall@k>=0.85": metrics["recall"] >= THRESHOLDS["recall"],
        "Hit@k>=0.90": metrics["hit"] >= THRESHOLDS["hit"],
        "Faithfulness>=0.90": metrics["faithfulness"] >= THRESHOLDS["faithfulness"],
        "Citation>=0.90": metrics["citation"] >= THRESHOLDS["citation"],
        "幻觉率<=5%": metrics["hallucination"] <= THRESHOLDS["hallucination"],
        "无答案拒答=100%": metrics["refusalAccuracy"] >= THRESHOLDS["refusal"],
        "误拒答<=5%": metrics["falseRefusalRate"] <= THRESHOLDS["false_refusal"],
    }
    return all(checks.values()), checks


def main() -> None:
    corpus = load_jsonl(GOLDEN_DIR / "corpus.jsonl")
    items = load_jsonl(GOLDEN_DIR / "golden_set.jsonl")
    encoder = build_encoder(corpus)
    kbs = sorted({row["kb_id"] for row in corpus})
    access = R.AccessContext(
        user_id="acceptance-runner",
        tenant_id="default",
        roles=("platform_admin",),
        permissions=frozenset({R.PERMISSION_RETRIEVAL_QUERY}),
        kb_permissions={kb: frozenset({R.PERMISSION_RETRIEVAL_QUERY}) for kb in kbs},
        max_security_level="SECRET",
    )

    metrics, details = evaluate_with_encoder(items, corpus, encoder, access, TOP_K)
    passed, checks = gate(metrics)

    # 参数调优：top_k 扫描
    sweep = []
    for top_k in (3, 5, 8, 10):
        sweep_metrics, _ = evaluate_with_encoder(items, corpus, encoder, access, top_k)
        sweep.append(
            {
                "topK": top_k,
                "recall": sweep_metrics["recall"],
                "hit": sweep_metrics["hit"],
                "citation": sweep_metrics["citation"],
                "hallucination": sweep_metrics["hallucination"],
            }
        )

    # 编码器消融：内置哈希编码器 vs TF-IDF 字符 n-gram
    encoder_rows = [{"encoder": "tfidf-char-ngram-v1", **{k: metrics[k] for k in ("recall", "hit", "citation")}}]
    hashing_cls = getattr(R, "HashingQueryEncoder", None)
    if hashing_cls is not None:
        hashing = hashing_cls()
        hashing_metrics, _ = evaluate_with_encoder(items, corpus, hashing, access, TOP_K)
        encoder_rows.append(
            {
                "encoder": "hashing-baseline",
                **{k: hashing_metrics[k] for k in ("recall", "hit", "citation")},
            }
        )

    distribution = Counter(item["type"] for item in items)
    payload = {
        "generatedAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "goldenSetSize": len(items),
        "distribution": dict(distribution),
        "corpusSize": len(corpus),
        "topK": TOP_K,
        "metrics": metrics,
        "thresholds": THRESHOLDS,
        "gatePassed": passed,
        "gateChecks": checks,
        "tuning": {"topKSweep": sweep, "encoderAblation": encoder_rows},
        "stub": {
            "llm": "ExtractiveStubLLM (deterministic)",
            "note": "真实 LLM 属后置期依赖；检索与生成编排使用生产代码路径",
        },
    }

    out_dir = ACCEPTANCE_DIR / "quality"
    write_json(out_dir / "golden_eval_result.json", payload)
    write_jsonl(out_dir / "golden_eval_items.jsonl", details)

    rows = "\n".join(
        f"| {row['encoder']} | {row['recall']:.3f} | {row['hit']:.3f} | {row['citation']:.3f} |"
        for row in encoder_rows
    )
    sweep_rows = "\n".join(
        f"| {row['topK']} | {row['recall']:.3f} | {row['hit']:.3f} | {row['citation']:.3f} | {row['hallucination']:.3f} |"
        for row in sweep
    )
    failures = [d for d in details if d["answerable"] and not d["hit"]]
    md = f"""# P0 质量门禁复算报告

- 生成时间：{payload['generatedAt']}
- golden set：{len(items)} 条（标准编号直达 {distribution['standard_no_direct']} / 条款定位 {distribution['clause_locate']} / 跨条款归纳 {distribution['cross_clause']} / 无答案拒答 {distribution['no_answer']}）
- 语料：{len(corpus)} 条结构化条款（5 部金融/安全标准）
- 检索：生产 `RetrievalService`（top_k={TOP_K}，TF-IDF 字符 n-gram 编码器）
- 生成：生产 `GenerationService` + 确定性抽取式桩（真实 LLM 属后置期依赖）

## 指标

| 指标 | 实测 | 门槛 | 结论 |
| --- | --- | --- | --- |
| Recall@{TOP_K} | {metrics['recall']:.3f} | ≥0.85 | {'通过' if checks['Recall@k>=0.85'] else '不通过'} |
| Hit@{TOP_K} | {metrics['hit']:.3f} | ≥0.90 | {'通过' if checks['Hit@k>=0.90'] else '不通过'} |
| Faithfulness | {metrics['faithfulness']:.3f} | ≥0.90 | {'通过' if checks['Faithfulness>=0.90'] else '不通过'} |
| Citation | {metrics['citation']:.3f} | ≥0.90 | {'通过' if checks['Citation>=0.90'] else '不通过'} |
| 幻觉率 | {metrics['hallucination']:.3f} | ≤5% | {'通过' if checks['幻觉率<=5%'] else '不通过'} |
| 无答案拒答率 | {metrics['refusalAccuracy']:.3f} | =100% | {'通过' if checks['无答案拒答=100%'] else '不通过'} |
| 误拒答率 | {metrics['falseRefusalRate']:.3f} | ≤5% | {'通过' if checks['误拒答<=5%'] else '不通过'} |

- 端到端 P50：{metrics['p50LatencyMs']} ms；P95：{metrics['p95LatencyMs']} ms（离线桩）

## 参数调优（top_k）

| top_k | Recall | Hit | Citation | 幻觉率 |
| --- | --- | --- | --- | --- |
{sweep_rows}

## 编码器消融

| 编码器 | Recall | Hit | Citation |
| --- | --- | --- | --- |
{rows}

## 未命中样例

{('无' if not failures else chr(10).join(f"- {d['id']} {d['query']} 检索到 {d['retrievedClauseIds'][:3]}") )}

## 结论

{'质量门禁通过' if passed else '质量门禁未通过，见缺陷清单'}。
"""
    (out_dir / "GOLDEN-EVAL-REPORT.md").write_text(md, encoding="utf-8")
    print(md)
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
