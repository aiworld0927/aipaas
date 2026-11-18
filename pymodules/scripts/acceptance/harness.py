"""P0 集成验收公共 harness（离线、可复现）。

组成：
- ``TokenEncoder``：字符 bigram + 英文/编号词 TF-IDF 哈希编码器（无需外部模型，确定性）；
- 语料/索引/检索服务装配（复用生产 ``aipaas_retrieval``）；
- ``ExtractiveStubLLM``：抽取式答案桩，仅供离线验收，标记为「依赖后置期能力」；
- ``RetrievalAdapter``：把生产检索服务结果映射为生成层 ``RetrievedClause``。

注意：真实 LLM 与 Milvus 属后置期依赖，本 harness 用确定性桩替身，
以便验收证据可重复计算；报告须列明该边界。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import aipaas_retrieval as R
import aipaas_generation as G

WORKSPACE = Path(__file__).resolve().parents[3]
ACCEPTANCE_DIR = WORKSPACE / "docs" / "acceptance"
GOLDEN_DIR = ACCEPTANCE_DIR / "golden"

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_ASCII = re.compile(r"[A-Za-z0-9]+(?:[/.\-][A-Za-z0-9]+)*")
_ASCII_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "are", "was", "how",
    "what", "which", "when", "where", "why", "does", "did", "into", "than",
}


def content_tokens(text: str) -> list[str]:
    """内容 token：中文 bigram（单字保留）+ 英文/标准编号词。"""
    text = (text or "").lower()
    tokens: list[str] = []
    for run in _CJK.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    for word in _ASCII.findall(text):
        if len(word) >= 2 and word not in _ASCII_STOP:
            tokens.append(word)
    return tokens


@dataclass
class TokenEncoder:
    """确定性 TF-IDF 哈希编码器；``fit`` 用语料统计 IDF。"""

    dimension: int = 512
    idf: dict[str, float] | None = None
    _df: dict[str, int] = None  # type: ignore[assignment]
    _docs: int = 0

    def fit(self, texts: Iterable[str]) -> "TokenEncoder":
        df: dict[str, int] = {}
        docs = 0
        for text in texts:
            docs += 1
            for token in set(content_tokens(text)):
                df[token] = df.get(token, 0) + 1
        self._docs = max(1, docs)
        self._df = df
        self.idf = {
            token: math.log((self._docs + 1) / (count + 1)) + 1.0
            for token, count in df.items()
        }
        return self

    def _bucket(self, token: str) -> int:
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % self.dimension

    def encode(self, text: str) -> list[float]:
        if self.idf is None:
            raise RuntimeError("TokenEncoder 必须先 fit")
        vector = [0.0] * self.dimension
        counts: dict[str, int] = {}
        for token in content_tokens(text):
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            weight = self.idf.get(token, 1.0) * (1.0 + math.log(count))
            vector[self._bucket(token)] += weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def write_jsonl(path: Path, items: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_encoder(corpus: Sequence[Mapping[str, Any]]) -> TokenEncoder:
    encoder = TokenEncoder()
    encoder.fit([str(item.get("text", "")) for item in corpus])
    return encoder


def build_searcher(
    corpus: Sequence[Mapping[str, Any]], encoder: TokenEncoder
) -> R.InMemoryVectorSearcher:
    searcher = R.InMemoryVectorSearcher()
    records = [
        R.VectorRecord(
            chunk_id=item["chunk_id"],
            document_id=item["document_id"],
            kb_id=item["kb_id"],
            text=item["text"],
            vector=encoder.encode(item["text"]),
            tenant_id=item.get("tenant_id", "default"),
            clause_no=item.get("clause_no"),
            clause_title=item.get("clause_title"),
            chapter_path=tuple(item.get("chapter_path") or ()),
            page_no=item.get("page_no"),
            standard_no=item.get("standard_no"),
            standard_name=item.get("standard_name"),
            issuer=item.get("issuer"),
            domain=item.get("domain"),
            effective_at=item.get("effective_at"),
            security_level=item.get("security_level", "PUBLIC"),
        )
        for item in corpus
    ]
    searcher.upsert(records)
    return searcher


def accept_all_access(kb_ids: Iterable[str]) -> R.AccessContext:
    kb_permissions = {
        kb_id: frozenset({R.PERMISSION_RETRIEVAL_QUERY}) for kb_id in kb_ids
    }
    return R.AccessContext(
        user_id="acceptance-runner",
        tenant_id="default",
        roles=("platform_admin",),
        permissions=frozenset({R.PERMISSION_RETRIEVAL_QUERY}),
        kb_permissions=kb_permissions,
        max_security_level="SECRET",
    )


def build_retrieval_service(
    searcher: R.VectorSearcher,
    encoder: TokenEncoder,
    *,
    audit: R.AuditSink | None = None,
    config: R.RetrievalConfig | None = None,
) -> R.RetrievalService:
    return R.RetrievalService(
        searcher=searcher,
        encoder=encoder,
        audit=audit or R.InMemoryAuditSink(),
        config=config or R.RetrievalConfig(),
    )


class RetrievalAdapter:
    """生成层检索端口 -> 生产检索服务。"""

    def __init__(self, service: R.RetrievalService) -> None:
        self._service = service

    def search(
        self,
        query: str,
        access: Any,
        *,
        top_k: int,
        kb_id: str | None,
        kb_ids: list[str],
        filters: Any | None,
        trace_id: str | None,
    ) -> list[G.RetrievedClause]:
        if isinstance(filters, R.RetrievalFilters):
            retrieval_filters = filters
        elif isinstance(filters, Mapping):
            retrieval_filters = R.RetrievalFilters(
                standard_no=filters.get("standard_no") or filters.get("standardNo"),
                clause_no=filters.get("clause_no") or filters.get("clauseNo"),
                issuer=filters.get("issuer"),
                domain=filters.get("domain"),
            )
        else:
            retrieval_filters = R.RetrievalFilters()
        request = R.RetrieveRequest(
            query=query,
            kb_id=kb_id,
            kb_ids=list(kb_ids or ([kb_id] if kb_id else [])),
            top_k=top_k,
            retrieval_mode="vector",
            rerank=False,
            filters=retrieval_filters,
            trace_id=trace_id,
        )
        result = self._service.retrieve(request, access)
        def chapter_text(value: Any) -> str:
            if isinstance(value, (list, tuple)):
                return " / ".join(str(item) for item in value)
            return str(value or "")

        return [
            G.RetrievedClause(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                text=hit.text,
                kb_id=hit.kb_id,
                clause_no=hit.clause_no,
                standard_no=hit.standard_no,
                standard_name=hit.standard_name,
                chapter_path=chapter_text(hit.chapter_path),
                page_no=hit.page_no,
                score=hit.score,
                security_level=hit.security_level,
                rank=hit.rank,
            )
            for hit in result.hits
        ]


_BLOCK = re.compile(r"^\[(\d+)\]\s+([^\n]*)\n([^\n]+)", re.M)
_NOISE = re.compile(
    r"(?:GB/T|JR/T|GB|ISO|IEC)\s*\d+[\d.\-]*|第\s*\d+(?:\.\d+)*\s*条|\d+(?:\.\d+)+"
)
_QUESTION_STOP = {
    "如何", "什么", "哪些", "哪个", "主要", "规定", "要求", "内容", "范围",
    "适用", "是否", "需要", "实现", "包括", "分别", "相关", "标准", "条款",
    "请给", "给出", "请问", "怎么", "怎样", "可以", "进行", "环节", "方面",
    "针对", "关于", "对于", "以及", "及其", "按照", "根据", "合规", "阶段",
    "满足", "有关", "情形", "义务", "方式",
}
GENERIC_TOKENS = _QUESTION_STOP
_PARTICLES = re.compile(r"[的了吗呢是在和与或及对中请]")


def strip_generic(text: str) -> str:
    cleaned = text
    for token in sorted(_QUESTION_STOP, key=len, reverse=True):
        cleaned = cleaned.replace(token, " ")
    return _PARTICLES.sub(" ", cleaned)


@dataclass
class ExtractiveStubLLM:
    """抽取式答案桩：只从检索条款原文摘抄，超出支持度则拒答。

    该桩替代真实 LLM（后置期依赖），保证离线复算稳定；它同时模拟
    "无证据即拒答" 的受约束生成行为。
    """

    support_threshold: float = 0.30
    max_clauses: int = 4
    idf: dict[str, float] | None = None

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        provider: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **_: Any,
    ) -> G.LLMResponse:
        prompt = "\n".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "user"
        )
        question = ""
        match = re.search(r"【用户问题】\s*(.*?)(?:\n\n请作答：|\Z)", prompt, re.S)
        if match:
            question = match.group(1).strip()
        blocks = _BLOCK.findall(prompt)
        if not blocks:
            content = ""
        else:
            support = self._support(question, [body for _, _, body in blocks])
            if support < self.support_threshold:
                content = ""
            else:
                lines = [
                    f"- {body.strip()} [{index}]"
                    for index, _, body in blocks[: self.max_clauses]
                ]
                content = "\n".join(lines)
        return G.LLMResponse(
            provider=provider or "stub-extractive",
            model="stub-extractive-v1",
            content=content,
            usage=G.TokenUsage(
                prompt_tokens=max(1, len(prompt) // 4),
                completion_tokens=max(1, len(content) // 4),
                total_tokens=max(2, (len(prompt) + len(content)) // 4),
            ),
            latency_ms=1,
            degraded=True,
            attempts=1,
            warnings=["STUB_LLM: 抽取式桩，真实 LLM 属后置期依赖"],
        )

    def stream(self, *args: Any, **kwargs: Any):  # pragma: no cover - 验收未使用流式
        raise NotImplementedError("验收 harness 不覆盖流式桩")

    def _support(self, question: str, bodies: Sequence[str]) -> float:
        cleaned = strip_generic(_NOISE.sub(" ", question))
        ask = set(content_tokens(cleaned))
        if not ask:
            return 1.0
        evidence: set[str] = set()
        for body in bodies:
            evidence.update(content_tokens(body))
        idf = self.idf or {}
        default_weight = max(idf.values()) if idf else 1.0
        total = sum(idf.get(token, default_weight) for token in ask)
        matched = sum(idf.get(token, default_weight) for token in ask & evidence)
        return matched / total if total else 1.0


def build_generation_service(
    retrieval_service: R.RetrievalService,
    *,
    llm: Any | None = None,
    config: G.GenerationConfig | None = None,
) -> G.GenerationService:
    return G.GenerationService(
        retriever=RetrievalAdapter(retrieval_service),
        llm=llm or ExtractiveStubLLM(),
        config=config
        or G.GenerationConfig(
            max_top_k=10,
            max_clauses_in_prompt=8,
            temperature=0.0,
            max_tokens=1024,
        ),
    )


def make_service_bundle(
    corpus: Sequence[Mapping[str, Any]],
    *,
    searcher: R.VectorSearcher | None = None,
    llm: Any | None = None,
):
    """返回 (encoder, searcher, retrieval, generation)。"""
    encoder = build_encoder(corpus)
    searcher = searcher or build_searcher(corpus, encoder)
    retrieval = build_retrieval_service(searcher, encoder)
    generation = build_generation_service(retrieval, llm=llm)
    return encoder, searcher, retrieval, generation
