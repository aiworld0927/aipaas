from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from aipaas_pipeline.samples import md_gbt, pdf_gbt_35273, xlsx_gbt_clauses

_DATASETS = [
    ("pdf", "sample.pdf", pdf_gbt_35273()),
    ("md", "sample.md", md_gbt()),
    ("xlsx", "sample.xlsx", xlsx_gbt_clauses()),
]


def test_ten_plus_concurrent_parses_do_not_block(service) -> None:
    total = 12
    started = time.monotonic()

    def run(index: int) -> tuple[str, bool, int]:
        fmt, filename, data = _DATASETS[index % len(_DATASETS)]
        doc_id = f"concurrent-{index}"
        document = service.parse(data, doc_id=doc_id, filename=filename)
        return doc_id, document.success, len(document.clauses)

    with ThreadPoolExecutor(max_workers=total) as pool:
        results = list(pool.map(run, range(total)))

    elapsed = time.monotonic() - started
    assert all(success for _, success, _ in results)
    assert all(clauses > 0 for _, _, clauses in results)
    assert elapsed < 30, f"12 个并发解析耗时 {elapsed:.2f}s，疑似阻塞"
    assert len({doc_id for doc_id, _, _ in results}) == total
    assert all(service.chunk_count(doc_id) > 0 for doc_id, _, _ in results)
