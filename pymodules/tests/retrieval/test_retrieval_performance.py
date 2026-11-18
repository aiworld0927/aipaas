"""检索性能：P95 ≤ 500ms（含查询归一、向量召回、权限复核）。"""

from __future__ import annotations

import math
import time

from aipaas_retrieval import (
    HashingQueryEncoder,
    InMemoryAuditSink,
    InMemoryVectorSearcher,
    RetrievalConfig,
    RetrievalService,
    RetrieveRequest,
    VectorRecord,
)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def _corpus(encoder: HashingQueryEncoder, count: int = 300) -> list[VectorRecord]:
    words = ("风险评估", "客户身份识别", "数据安全", "反洗钱", "个人信息保护", "可疑交易")
    records: list[VectorRecord] = []
    for index in range(count):
        text = f"{index} {words[index % len(words)]} 条款要求 金融机构应当落实管理措施。"
        records.append(
            VectorRecord(
                chunk_id=f"chunk-{index:04d}",
                document_id=f"doc-{index // 10:03d}",
                kb_id="kb_a" if index % 2 == 0 else "kb_b",
                text=text,
                vector=encoder.encode(text),
                clause_no=f"{index % 9 + 1}.{index % 5 + 1}.{index % 3 + 1}",
                chapter_path=f"第{index % 9 + 1}章",
                standard_no="JR/T 0088-2016" if index % 2 == 0 else "JR/T 0197-2020",
                issuer="中国人民银行",
                domain="信息安全" if index % 3 else "反洗钱",
                effective_at="2017-06-01" if index % 2 == 0 else "2020-10-01",
            )
        )
    return records


def test_retrieval_p95_within_500ms() -> None:
    encoder = HashingQueryEncoder(dimension=64)
    searcher = InMemoryVectorSearcher()
    searcher.upsert(_corpus(encoder))
    service = RetrievalService(
        searcher=searcher,
        encoder=encoder,
        audit=InMemoryAuditSink(),
        config=RetrievalConfig(timeout_ms=300),
    )
    from aipaas_retrieval import AccessContext

    access = AccessContext(
        user_id="perf-user",
        permissions=frozenset({"retrieval:query"}),
        kb_permissions={
            "kb_a": frozenset({"retrieval:query"}),
            "kb_b": frozenset({"retrieval:query"}),
        },
    )
    queries = []
    for index in range(60):
        if index % 3 == 0:
            queries.append("JR/T 0088")
        elif index % 3 == 1:
            queries.append(f"第{index % 9 + 1}.1.1条")
        else:
            queries.append("风险评估与客户身份识别要求")

    latencies: list[float] = []
    try:
        for query in queries:
            started = time.perf_counter()
            result = service.retrieve(RetrieveRequest(query=query, top_k=10), access)
            latencies.append((time.perf_counter() - started) * 1000)
            assert result.degraded is False
            assert result.latency_ms <= 500
            assert result.timings["queryUnderstandingMs"] <= result.latency_ms + 1
    finally:
        service.close()

    assert _p95(latencies) <= 500
    assert max(latencies) <= 500
