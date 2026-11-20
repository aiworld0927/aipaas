"""生成 golden set 与对应语料（确定性，可重复执行）。

用法：
    cd pymodules && uv run python scripts/acceptance/build_golden.py
输出：
    docs/03_运维验收/09_黄金数据集/corpus.jsonl
    docs/03_运维验收/09_黄金数据集/golden_set.jsonl
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import (  # noqa: E402
    CLAUSE_INDEX,
    CLAUSE_LOCATE_TARGETS,
    CROSS_CLAUSE_CASES,
    NO_ANSWER_CASES,
    STANDARDS,
    build_corpus,
)
from harness import GOLDEN_DIR, write_jsonl  # noqa: E402


def clause_title(standard_no: str, clause_no: str) -> str:
    for standard in STANDARDS:
        if standard["standard_no"] != standard_no:
            continue
        for no, title, *_ in standard["clauses"]:
            if no == clause_no:
                return title
    raise KeyError((standard_no, clause_no))


def main() -> None:
    corpus = build_corpus()
    golden: list[dict] = []
    seq = 0

    def add(**item) -> None:
        nonlocal seq
        seq += 1
        item["id"] = f"G{seq:03d}"
        golden.append(item)

    # 1) 标准编号直达：每个标准 2 条
    for standard in STANDARDS:
        std = standard["standard_no"]
        kb = standard["kb_id"]
        add(
            type="standard_no_direct",
            query=f"{std} 的适用范围是什么？",
            kb_ids=[kb],
            filters={"standard_no": std},
            answerable=True,
            expected_clause_ids=[CLAUSE_INDEX[(std, "1")]],
            expected_standard_no=std,
            notes="按标准编号直达范围条款",
        )
        add(
            type="standard_no_direct",
            query=f"{std} 主要规定了哪些内容？",
            kb_ids=[kb],
            filters={"standard_no": std},
            answerable=True,
            expected_clause_ids=[CLAUSE_INDEX[(std, "1")]],
            expected_standard_no=std,
            notes="按标准编号直达并返回该标准条款",
        )

    # 2) 条款定位：20 条
    for std, clause_no in CLAUSE_LOCATE_TARGETS:
        title = clause_title(std, clause_no)
        kb = next(item["kb_id"] for item in STANDARDS if item["standard_no"] == std)
        add(
            type="clause_locate",
            query=f"{std} 第 {clause_no} 条“{title}”的要求是什么？",
            kb_ids=[kb],
            filters={"standard_no": std, "clause_no": clause_no},
            answerable=True,
            expected_clause_ids=[CLAUSE_INDEX[(std, clause_no)]],
            expected_standard_no=std,
            notes="标准编号 + 条款号定位",
        )

    # 3) 跨条款归纳：10 条
    for case in CROSS_CLAUSE_CASES:
        expected = [CLAUSE_INDEX[key] for key in case["expects"]]
        kb_ids = sorted({cid.split(":")[0].replace("doc_", "kb_", 1) for cid in expected})
        add(
            type="cross_clause",
            query=case["query"],
            kb_ids=kb_ids,
            filters={},
            answerable=True,
            expected_clause_ids=expected,
            expected_standard_no=None,
            notes="多条款归纳，统计 Recall@k 覆盖",
        )

    # 4) 无答案拒答：12 条
    for case in NO_ANSWER_CASES:
        add(
            type="no_answer",
            query=case["query"],
            kb_ids=[item["kb_id"] for item in STANDARDS],
            filters=case.get("filters") or {},
            answerable=False,
            expected_clause_ids=[],
            expected_standard_no=None,
            notes="应拒答，不得编造标准号/条款/数据",
        )

    write_jsonl(GOLDEN_DIR / "corpus.jsonl", corpus)
    write_jsonl(GOLDEN_DIR / "golden_set.jsonl", golden)
    counts: dict[str, int] = {}
    for item in golden:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    print(f"corpus={len(corpus)} golden={len(golden)} distribution={counts}")


if __name__ == "__main__":
    main()
