"""生成链路数据模型：引用、检索条款、LLM 响应与最终答案。

字段命名与 ``aipaas-openapi-v1.yaml`` 的 ``Citation`` / ``Answer`` / ``TokenUsage``
对齐，``to_dict()`` 输出 camelCase。生成结果必须携带可解析引用
（``chunkId`` / ``documentId`` / ``clauseNo``），供答案后处理与前端展示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

ANSWER_FORMAT_MARKDOWN = "MARKDOWN"
FINISH_REASONS = ("STOP", "LENGTH", "CONTENT_FILTER", "REFUSED", "ERROR")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "promptTokens": int(self.prompt_tokens),
            "completionTokens": int(self.completion_tokens),
            "totalTokens": int(self.total_tokens),
        }


@dataclass(slots=True)
class Citation:
    """可解析引用：chunkId/documentId 必填，clauseNo 用于与条款编号对应。"""

    chunk_id: str
    document_id: str
    clause_no: str | None = None
    standard_no: str | None = None
    standard_name: str | None = None
    chapter_path: str | None = None
    page_no: int | None = None
    quote: str | None = None
    score: float | None = None

    def key(self) -> tuple[str, str | None]:
        return (self.chunk_id, self.clause_no)

    def label(self) -> str:
        parts = [self.standard_no or "来源文档", self.clause_no or self.document_id]
        return " ".join(part for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            # 兼容 #106 验收口径中的 docId 命名；契约主字段仍为 documentId。
            "docId": self.document_id,
            "standardNo": self.standard_no,
            "standardName": self.standard_name,
            "clauseNo": self.clause_no,
            "pageNo": self.page_no,
            "chapterPath": self.chapter_path,
            "quote": self.quote,
            "score": None if self.score is None else round(float(self.score), 6),
        }


@dataclass(slots=True)
class RetrievedClause:
    """生成侧使用的检索条款；由检索结果映射，避免生成层依赖检索实现细节。"""

    chunk_id: str
    document_id: str
    text: str
    kb_id: str = ""
    clause_no: str | None = None
    standard_no: str | None = None
    standard_name: str | None = None
    chapter_path: str | None = None
    page_no: int | None = None
    score: float = 0.0
    security_level: str = "PUBLIC"
    rank: int = 0

    @classmethod
    def from_retrieval_hit(cls, hit: Any) -> "RetrievedClause":
        """接受 ``RetrieveHit``（鸭子类型），不导入检索包以保持生成层可独立测试。"""
        return cls(
            chunk_id=str(getattr(hit, "chunk_id", "") or ""),
            document_id=str(getattr(hit, "document_id", "") or ""),
            text=str(getattr(hit, "text", "") or ""),
            kb_id=str(getattr(hit, "kb_id", "") or ""),
            clause_no=getattr(hit, "clause_no", None),
            standard_no=getattr(hit, "standard_no", None),
            standard_name=getattr(hit, "standard_name", None),
            chapter_path=getattr(hit, "chapter_path", None),
            page_no=getattr(hit, "page_no", None),
            score=float(getattr(hit, "score", 0.0) or 0.0),
            security_level=str(getattr(hit, "security_level", "PUBLIC") or "PUBLIC"),
            rank=int(getattr(hit, "rank", 0) or 0),
        )

    def header(self) -> str:
        """Prompt 中的条款头：标准号 + 条款号 + 章节路径，便于模型按编号引用。"""
        segments = [self.standard_no or "未标注标准号"]
        if self.clause_no:
            segments.append(f"第 {self.clause_no} 条")
        if self.standard_name:
            segments.append(self.standard_name)
        if self.chapter_path:
            segments.append(self.chapter_path)
        return " | ".join(segments)

    def citation(self) -> Citation:
        return Citation(
            chunk_id=self.chunk_id,
            document_id=self.document_id,
            clause_no=self.clause_no,
            standard_no=self.standard_no,
            standard_name=self.standard_name,
            chapter_path=self.chapter_path,
            page_no=self.page_no,
            quote=(self.text[:120] or None),
            score=self.score,
        )


@dataclass(slots=True)
class LLMResponse:
    """供应商返回：``attempts`` 记录脱敏后的尝试链，供降级提示与排障。"""

    provider: str
    model: str
    content: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    degraded: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnswerRequest:
    """问答入参；``kbId`` 与 ``kbIds`` 互斥，P1/P2 字段接收但不保持上下文。"""

    query: str
    kb_id: str | None = None
    kb_ids: list[str] = field(default_factory=list)
    top_k: int = 5
    filters: Any | None = None
    session_id: str | None = None
    provider: str | None = None
    trace_id: str | None = None


@dataclass(slots=True)
class Answer:
    """最终答案：拒答时 ``refused=true``、``content`` 为可配置引导语，绝不编造。"""

    answer_id: str
    tenant_id: str
    content: str
    citations: list[Citation] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    confidence: float = 0.0
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "STOP"
    format: str = ANSWER_FORMAT_MARKDOWN
    retrieval_mode: str = "vector"
    rerank: bool = False
    prompt_template_version: str | None = None
    provider: str | None = None
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    session_id: str | None = None
    trace_id: str | None = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answerId": self.answer_id,
            "tenantId": self.tenant_id,
            "sessionId": self.session_id,
            "content": self.content,
            "format": self.format,
            "citations": [citation.to_dict() for citation in self.citations],
            "refused": self.refused,
            "refusalReason": self.refusal_reason,
            "confidence": round(float(self.confidence), 4),
            "usage": self.usage.to_dict(),
            "finishReason": self.finish_reason,
            "retrievalMode": self.retrieval_mode,
            "rerank": self.rerank,
            "promptTemplateVersion": self.prompt_template_version,
            "provider": self.provider,
            "degraded": self.degraded,
            "warnings": list(self.warnings),
            "timings": {key: round(value, 3) for key, value in self.timings.items()},
            "traceId": self.trace_id,
            "createdAt": self.created_at,
        }


def message(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def validate_messages(messages: Any) -> list[dict[str, str]]:
    """规范化 LLM 消息：角色仅允许 system/user/assistant，内容非空。"""
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("messages 不能为空")
    normalized: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, Mapping):
            raise ValueError("messages 元素必须包含 role/content")
        role = str(item.get("role", "")).strip()
        content = item.get("content", "")
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"不支持的消息角色：{role or '空'}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("消息内容不能为空")
        normalized.append(message(role, content))
    return normalized
