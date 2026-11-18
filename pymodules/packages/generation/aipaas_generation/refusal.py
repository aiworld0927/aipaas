"""#108 拒答与兜底：无答案/低置信度时明确拒答，给出可理解的查阅指引。

- 阈值（``confidence_threshold``、``min_citations``）与全部文案均可配置；
- 拒答原因使用稳定编码（便于统计），同时给出用户可理解的中文理由；
- ``RefusalStats`` 按原因/租户累计，供管理端观察拒答率。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Sequence

from aipaas_generation.models import RetrievedClause

REASON_NO_EVIDENCE = "NO_RETRIEVAL_RESULT"
REASON_LOW_CONFIDENCE = "LOW_CONFIDENCE"
REASON_NO_CITATION = "NO_CITATION"
REASON_EMPTY_ANSWER = "EMPTY_ANSWER"
REASON_LLM_UNAVAILABLE = "LLM_UNAVAILABLE"

DEFAULT_GUIDANCE = (
    "建议查阅相关标准的原文；也可提供标准编号（如 JR/T 0088）或条款编号（如 4.1.1）以便精确定位。"
)


@dataclass(slots=True)
class RefusalMessages:
    """拒答/兜底文案；每个分支独立可配置，避免统一“抱歉”式敷衍。"""

    no_evidence: str = "未检索到与该问题相关的标准条款，无法给出有依据的回答。"
    low_confidence: str = "检索到的条款与该问题的相关性较低，无法给出可靠回答。"
    no_citation: str = "生成的回答未能对应到具体条款，为避免误导，暂不提供结论。"
    empty_answer: str = "模型未返回可用内容，请稍后重试。"
    model_unavailable: str = "模型服务暂不可用，请稍后重试。"
    guidance: str = DEFAULT_GUIDANCE


@dataclass(slots=True)
class RefusalDecision:
    refused: bool
    reason_code: str | None = None
    reason_text: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "refused": self.refused,
            "reasonCode": self.reason_code,
            "reasonText": self.reason_text,
            "message": self.message,
        }


@dataclass(slots=True)
class RefusalPolicy:
    """拒答判定：按“无检索结果 → 空答案 → 无引用 → 低置信度”顺序给出原因。"""

    confidence_threshold: float = 0.35
    min_citations: int = 1
    messages: RefusalMessages = field(default_factory=RefusalMessages)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold 必须在 0..1 之间")
        if self.min_citations < 0:
            raise ValueError("min_citations 不能为负")

    def _message(self, text: str) -> str:
        guidance = self.messages.guidance
        return f"{text}\n\n{guidance}" if guidance else text

    def decide(
        self,
        *,
        has_clauses: bool,
        citation_count: int,
        confidence: float,
        answer_empty: bool = False,
    ) -> RefusalDecision:
        if not has_clauses:
            return RefusalDecision(
                refused=True,
                reason_code=REASON_NO_EVIDENCE,
                reason_text="检索结果中没有可用条款",
                message=self._message(self.messages.no_evidence),
            )
        if answer_empty:
            return RefusalDecision(
                refused=True,
                reason_code=REASON_EMPTY_ANSWER,
                reason_text="模型返回内容为空",
                message=self._message(self.messages.empty_answer),
            )
        if citation_count < max(1, self.min_citations):
            return RefusalDecision(
                refused=True,
                reason_code=REASON_NO_CITATION,
                reason_text="答案未能对应到检索条款",
                message=self._message(self.messages.no_citation),
            )
        if confidence < self.confidence_threshold:
            return RefusalDecision(
                refused=True,
                reason_code=REASON_LOW_CONFIDENCE,
                reason_text=(
                    f"置信度 {confidence:.2f} 低于阈值 {self.confidence_threshold:.2f}"
                ),
                message=self._message(self.messages.low_confidence),
            )
        return RefusalDecision(refused=False)

    def model_unavailable(self) -> RefusalDecision:
        return RefusalDecision(
            refused=True,
            reason_code=REASON_LLM_UNAVAILABLE,
            reason_text="全部 LLM 供应商不可用",
            message=self._message(self.messages.model_unavailable),
        )


class RefusalStats:
    """拒答统计（内存累计）：总数、按原因、按租户，便于计算拒答率。"""

    def __init__(self) -> None:
        self._answered = 0
        self._by_reason: dict[str, int] = {}
        self._by_tenant: dict[str, int] = {}
        self._lock = threading.RLock()

    def record_refusal(self, reason_code: str, *, tenant_id: str = "default") -> None:
        with self._lock:
            self._by_reason[reason_code] = self._by_reason.get(reason_code, 0) + 1
            self._by_tenant[tenant_id] = self._by_tenant.get(tenant_id, 0) + 1

    def record_answer(self) -> None:
        with self._lock:
            self._answered += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            decided = self._answered + sum(self._by_reason.values())
            return {
                "answered": self._answered,
                "refused": sum(self._by_reason.values()),
                "total": decided,
                "refusalRate": round(
                    (sum(self._by_reason.values()) / decided) if decided else 0.0, 4
                ),
                "byReason": dict(sorted(self._by_reason.items())),
                "byTenant": dict(sorted(self._by_tenant.items())),
            }

    def reset(self) -> None:
        with self._lock:
            self._answered = 0
            self._by_reason.clear()
            self._by_tenant.clear()


def estimate_confidence(
    clauses: Sequence[RetrievedClause], cited_clause_ids: Sequence[str]
) -> float:
    """置信度 = 0.7 × 引用条款平均检索分 + 0.3 × 引用覆盖度；无引用即 0。"""
    if not clauses or not cited_clause_ids:
        return 0.0
    by_id = {clause.chunk_id: clause for clause in clauses}
    scores = [
        max(0.0, min(1.0, float(by_id[chunk_id].score)))
        for chunk_id in cited_clause_ids
        if chunk_id in by_id
    ]
    if not scores:
        return 0.0
    evidence = sum(scores) / len(scores)
    coverage = min(1.0, len(scores) / min(2, len(clauses)))
    return round(max(0.0, min(1.0, 0.7 * evidence + 0.3 * coverage)), 4)
