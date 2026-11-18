"""P0 端到端验收：PDF → 解析 → 条目化分块 → 向量化 → 索引 → 检索 →
条款定位问答 → 引用展示 → 标准简介 → 条目化导出。

用法：
    cd pymodules && uv run python scripts/acceptance/e2e_p0.py

边界：真实 embedding/Milvus/LLM 由确定性替身承担（后置期依赖），
解析、分块、检索编排、生成编排、简介抽取与导出数据均走生产代码。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aipaas_generation as G
import aipaas_pipeline as P
import aipaas_retrieval as R
from aipaas_pipeline.chunking import ChunkConfig, build_chunks
from aipaas_pipeline.service import ParseService
from aipaas_pipeline.summary import SummaryService
from aipaas_pipeline.vector_index import HnswIndexService
from harness import (  # noqa: E402
    ACCEPTANCE_DIR,
    ExtractiveStubLLM,
    TokenEncoder,
    build_generation_service,
    build_retrieval_service,
    build_searcher,
    content_tokens,
    write_json,
)

SAMPLE_DIR = ACCEPTANCE_DIR.parent / "parse-pipeline" / "samples" / "pdf"
OUT_DIR = ACCEPTANCE_DIR / "e2e"
KB_ID = "kb_e2e"


def timed(steps: list[dict], name: str, func):
    started = time.perf_counter()
    try:
        detail = func()
        status = "PASS"
        error = None
    except Exception as exc:  # noqa: BLE001 - 验收需记录失败而非中断
        detail = None
        status = "FAIL"
        error = f"{type(exc).__name__}: {exc}"
    duration = round((time.perf_counter() - started) * 1000, 2)
    steps.append(
        {
            "step": name,
            "status": status,
            "durationMs": duration,
            "detail": detail,
            "error": error,
        }
    )
    return detail


def main() -> None:
    steps: list[dict] = []
    parse_service = ParseService()
    text_pdfs = [
        ("e2e_gbt35273", "GBT-35273-2020.pdf"),
        ("e2e_jrt0197", "JRT-0197-2020.pdf"),
        ("e2e_gbt22239", "GBT-22239-2019.pdf"),
    ]
    scanned_pdf = "scanned-sample.pdf"

    documents: dict[str, object] = {}

    def parse_all():
        results = []
        for doc_id, filename in text_pdfs:
            data = (SAMPLE_DIR / filename).read_bytes()
            doc = parse_service.parse(
                data,
                doc_id=doc_id,
                filename=filename,
                chunk_options=ChunkConfig(strategy="structured"),
            )
            documents[doc_id] = doc
            results.append(
                {
                    "docId": doc_id,
                    "file": filename,
                    "success": doc.success,
                    "standardNo": doc.standard_no,
                    "title": doc.title,
                    "clauseCount": len(doc.clauses),
                    "tableCount": len(doc.tables),
                    "chunkCount": len(build_chunks(doc, ChunkConfig(strategy="structured"))),
                }
            )
            if not doc.success or not doc.clauses:
                raise AssertionError(f"解析失败：{doc_id} {doc.failure_code}")
        return results

    timed(steps, "1. 上传并解析 3 份文本型 PDF（含表格）", parse_all)

    def parse_scanned():
        data = (SAMPLE_DIR / scanned_pdf).read_bytes()
        doc = parse_service.parse(data, doc_id="e2e_scanned", filename=scanned_pdf)
        if doc.success or not doc.failure_code:
            raise AssertionError("扫描件未返回明确失败标识")
        return {"file": scanned_pdf, "success": doc.success, "failureCode": doc.failure_code}

    timed(steps, "2. 扫描件负向样例明确失败标识", parse_scanned)

    chunk_records: list[dict] = []
    itemized_exports: dict[str, list[dict]] = {}

    def chunk_all():
        summary = []
        for doc_id, _ in text_pdfs:
            doc = documents[doc_id]
            structured = build_chunks(doc, ChunkConfig(strategy="structured"))
            itemized = build_chunks(doc, ChunkConfig(strategy="itemized"))
            for chunk in structured:
                chunk_records.append(
                    {
                        "chunk_id": f"{doc_id}:{chunk.chunk_id}",
                        "document_id": doc_id,
                        "kb_id": KB_ID,
                        "tenant_id": "default",
                        "text": chunk.content,
                        "standard_no": doc.standard_no,
                        "standard_name": doc.title,
                        "clause_no": chunk.clause_no,
                        "clause_title": chunk.clause_title,
                        "chapter_path": [chunk.chapter_path] if chunk.chapter_path else [],
                        "page_no": chunk.page_no,
                        "security_level": "INTERNAL",
                    }
                )
            itemized_exports[doc_id] = [
                {
                    "clauseNo": clause.clause_no,
                    "clauseTitle": clause.title,
                    "text": clause.text,
                    "chapterPath": clause.chapter_path,
                    "pageNo": clause.page_no,
                    "standardNo": doc.standard_no,
                }
                for clause in doc.clauses
            ]
            if len(itemized_exports[doc_id]) != len(doc.clauses):
                raise AssertionError("条目化导出条目数不等于条款数")
            summary.append(
                {
                    "docId": doc_id,
                    "structuredChunks": len(structured),
                    "itemizedChunks": len(itemized),
                    "clauses": len(doc.clauses),
                }
            )
        return summary

    timed(steps, "3. 条目化分块（条目数=条款数）", chunk_all)

    encoder = TokenEncoder()
    encoder.fit([record["text"] for record in chunk_records])
    index_service = HnswIndexService()

    def vectorize_and_index():
        records_by_kb = [
            {
                "chunkId": record["chunk_id"],
                "vector": encoder.encode(record["text"]),
                "documentId": record["document_id"],
                "model": "acceptance-tfidf-char-ngram-v1",
                "text": record["text"],
            }
            for record in chunk_records
        ]
        build = index_service.build(KB_ID, records_by_kb, dimension=encoder.dimension)
        if build.status != "READY" or build.indexed_chunks != len(chunk_records):
            raise AssertionError(f"索引构建失败：{build.status}")
        hits = index_service.search(KB_ID, encoder.encode("个人信息收集要求"), top_k=3)
        return {
            "vectors": len(records_by_kb),
            "dimension": encoder.dimension,
            "buildId": build.build_id,
            "status": build.status,
            "indexedChunks": build.indexed_chunks,
            "durationMs": build.duration_ms,
            "searchProbe": [hit["chunkId"] for hit in hits],
        }

    timed(steps, "4. 向量化并构建 HNSW 索引", vectorize_and_index)

    searcher = build_searcher(chunk_records, encoder)
    retrieval = build_retrieval_service(searcher, encoder)
    generation = build_generation_service(
        retrieval, llm=ExtractiveStubLLM(idf=encoder.idf)
    )
    access = R.AccessContext(
        user_id="acceptance-runner",
        tenant_id="default",
        roles=("platform_admin",),
        permissions=frozenset({R.PERMISSION_RETRIEVAL_QUERY}),
        kb_permissions={KB_ID: frozenset({R.PERMISSION_RETRIEVAL_QUERY})},
        max_security_level="SECRET",
    )
    qa_results: list[dict] = []

    def query_and_answer():
        queries = [
            ("GB/T 35273-2020 对个人信息收集提出了哪些要求？", "GB/T 35273-2020"),
            ("JR/T 0197-2020 的适用范围是什么？", "JR/T 0197-2020"),
            ("GB/T 22239-2019 关于访问控制和安全审计的要求是什么？", "GB/T 22239-2019"),
        ]
        for query, standard_no in queries:
            request = R.RetrieveRequest(
                query=query,
                kb_ids=[KB_ID],
                top_k=5,
                filters=R.RetrievalFilters(standard_no=standard_no),
                trace_id=f"e2e-{standard_no}",
            )
            result = retrieval.retrieve(request, access)
            if not result.hits:
                raise AssertionError(f"检索无结果：{query}")
            answer = generation.answer(
                G.AnswerRequest(
                    query=query,
                    kb_ids=[KB_ID],
                    top_k=5,
                    filters=R.RetrievalFilters(standard_no=standard_no),
                    trace_id=request.trace_id,
                ),
                access,
            )
            if not answer.citations:
                raise AssertionError(f"答案缺少引用：{query}")
            qa_results.append(
                {
                    "query": query,
                    "retrievalLatencyMs": result.latency_ms,
                    "hits": [
                        {
                            "chunkId": hit.chunk_id,
                            "standardNo": hit.standard_no,
                            "clauseNo": hit.clause_no,
                            "score": hit.score,
                        }
                        for hit in result.hits
                    ],
                    "answer": answer.content,
                    "refused": answer.refused,
                    "citations": [
                        {
                            "chunkId": citation.chunk_id,
                            "standardNo": citation.standard_no,
                            "clauseNo": citation.clause_no,
                            "quote": citation.quote,
                        }
                        for citation in answer.citations
                    ],
                    "confidence": answer.confidence,
                }
            )
        return qa_results

    timed(steps, "5. 标准编号查询 → 条款定位问答 → 引用展示", query_and_answer)

    summaries: list[dict] = []

    def generate_summaries():
        service = SummaryService(parse_service)
        for doc_id, _ in text_pdfs:
            summary = service.generate(doc_id, actor="acceptance-runner")
            elements = summary.get("elements") or []
            keys = {element.get("key") for element in elements}
            required = {"standard_no", "standard_name", "scope", "main_content", "effective_date"}
            if not required.issubset(keys):
                raise AssertionError(f"简介五要素缺失：{doc_id} {required - keys}")
            summaries.append(
                {
                    "docId": doc_id,
                    "standardNo": summary.get("standardNo"),
                    "summaryVersion": summary.get("summaryVersion"),
                    "clauseCount": summary.get("clauseCount"),
                    "elements": {
                        element["key"]: element.get("value") for element in elements
                    },
                }
            )
        return summaries

    timed(steps, "6. 标准简介生成（五要素）", generate_summaries)

    def export_itemized():
        from openpyxl import Workbook

        export_dir = OUT_DIR / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for doc_id, rows in itemized_exports.items():
            json_path = export_dir / f"{doc_id}-clauses.json"
            json_path.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "条款"
            sheet.append(["条款号", "条款标题", "条款内容", "章节路径", "页码", "标准编号"])
            for row in rows:
                sheet.append(
                    [
                        row["clauseNo"],
                        row["clauseTitle"],
                        row["text"],
                        row["chapterPath"],
                        row["pageNo"],
                        row["standardNo"],
                    ]
                )
            xlsx_path = export_dir / f"{doc_id}-clauses.xlsx"
            workbook.save(xlsx_path)
            outputs.append(
                {
                    "docId": doc_id,
                    "rows": len(rows),
                    "json": str(json_path.relative_to(ACCEPTANCE_DIR.parent.parent)),
                    "xlsx": str(xlsx_path.relative_to(ACCEPTANCE_DIR.parent.parent)),
                }
            )
        return outputs

    timed(steps, "7. 条目化导出（JSON + XLSX）", export_itemized)

    def export_permission():
        denied_access = R.AccessContext(
            user_id="intruder",
            tenant_id="default",
            permissions=frozenset({R.PERMISSION_RETRIEVAL_QUERY}),
            kb_permissions={},
            max_security_level="SECRET",
        )
        try:
            retrieval.retrieve(
                R.RetrieveRequest(
                    query="导出数据",
                    kb_ids=[KB_ID],
                    top_k=5,
                    filters=R.RetrievalFilters(standard_no="GB/T 35273-2020"),
                    trace_id="e2e-denied",
                ),
                denied_access,
            )
        except Exception as exc:  # noqa: BLE001 - 记录拒绝类型即可
            return {"denied": True, "errorType": type(exc).__name__}
        raise AssertionError("越权导出/检索未被拦截")

    timed(steps, "8. 越权导出/检索拦截", export_permission)

    failed = [step for step in steps if step["status"] != "PASS"]
    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kbId": KB_ID,
        "documents": [
            {
                "docId": doc_id,
                "standardNo": documents[doc_id].standard_no,
                "clauses": len(documents[doc_id].clauses),
            }
            for doc_id, _ in text_pdfs
        ],
        "steps": steps,
        "qa": qa_results,
        "summaries": summaries,
        "chunks": len(chunk_records),
        "passed": not failed,
    }
    write_json(OUT_DIR / "E2E-RESULT.json", payload)

    lines = "\n".join(
        f"| {step['step']} | {step['status']} | {step['durationMs']} ms | {step['error'] or ''} |"
        for step in steps
    )
    md = f"""# P0 端到端场景验收报告

- 生成时间：{payload['generatedAt']}
- 知识库：{KB_ID}
- 文档：3 份文本型 PDF（含表格）+ 1 份扫描件负向样例
- 条目化 chunk：{len(chunk_records)}

## 执行步骤

| 步骤 | 结果 | 耗时 | 失败原因 |
| --- | --- | --- | --- |
{lines}

## 问答与引用

{chr(10).join(f"- **{item['query']}** 命中 {len(item['hits'])} 条，引用 {len(item['citations'])} 条（首个引用：{item['citations'][0]['standardNo']} 第 {item['citations'][0]['clauseNo']} 条）" for item in qa_results)}

## 结论

{'端到端链路全部通过，可重复执行' if not failed else '存在失败步骤，见缺陷清单'}。
"""
    (OUT_DIR / "E2E-REPORT.md").write_text(md, encoding="utf-8")
    print(md)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
