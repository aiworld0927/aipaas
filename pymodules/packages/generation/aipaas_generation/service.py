"""生成链路编排：#114 输入校验 → 检索 → #103 Prompt → #98 LLM → #106 引用/幻觉 →
#110 格式化 → #108 拒答兜底。

一次问答只做单轮（``sessionId`` 接收但不保持上下文）；检索失败、模型不可用时
均返回可理解的降级/拒答文案，绝不编造。审计只落元信息与 Prompt 指纹。
"""

from __future__ import annotations

import functools
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol, Sequence

from aipaas_common.core.observability import mark_generation, span

from aipaas_generation.audit import (
    ACTION_CHAT_QUERY,
    ACTION_INJECTION_BLOCKED,
    ACTION_INPUT_BLOCKED,
    AuditSink,
    audit_entry,
    safe_record,
)
from aipaas_generation.errors import InputBlocked, InvalidInput, ModelUnavailable
from aipaas_generation.formatting import MarkdownFormatter
from aipaas_generation.grounding import DEFAULT_ANNOTATION, GroundingChecker
from aipaas_generation.llm_client import LLMClientManager
from aipaas_generation.models import (
    ANSWER_FORMAT_MARKDOWN,
    Answer,
    AnswerRequest,
    RetrievedClause,
    TokenUsage,
    message,
)
from aipaas_generation.prompt_templates import (
    ContextWindowPolicy,
    PromptTemplateRegistry,
    assemble_prompt,
)
from aipaas_generation.refusal import (
    RefusalDecision,
    RefusalPolicy,
    RefusalStats,
    estimate_confidence,
)
from aipaas_generation.validation import (
    CATEGORY_PROMPT_INJECTION,
    InputValidator,
    ValidationPolicy,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class ClauseRetriever(Protocol):
    """检索端口：由适配器把检索服务结果映射为 ``RetrievedClause``。"""

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
    ) -> list[RetrievedClause]: ...


@dataclass(slots=True)
class GenerationConfig:
    """生成参数：阈值、窗口、文案全部可配置；不允许出现非法值。"""

    max_input_chars: int = 2000
    max_top_k: int = 20
    max_context_chars: int = 6000
    max_clauses_in_prompt: int = 8
    temperature: float = 0.1
    max_tokens: int = 1024
    max_segment_chars: int = 1200
    prompt_name: str = "rag-answer"
    prompt_version: str | None = None
    grounding_mode: str = "annotate"
    unsupported_annotation: str = DEFAULT_ANNOTATION
    answer_format: str = ANSWER_FORMAT_MARKDOWN
    refusal: RefusalPolicy = field(default_factory=RefusalPolicy)

    def __post_init__(self) -> None:
        if self.max_input_chars < 1:
            raise ValueError("max_input_chars 必须为正整数")
        if self.max_top_k < 1:
            raise ValueError("max_top_k 必须为正整数")
        if self.max_context_chars < 1:
            raise ValueError("max_context_chars 必须为正整数")
        if self.max_clauses_in_prompt < 1:
            raise ValueError("max_clauses_in_prompt 必须为正整数")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature 必须在 0..2 之间")
        if self.max_tokens < 1:
            raise ValueError("max_tokens 必须为正整数")


@dataclass(slots=True)
class _Prepared:
    """校验与检索结果：非流式与流式共用同一入口语义。"""

    query: str
    answer_id: str
    tenant_id: str
    clauses: list[RetrievedClause]
    retrieval_degraded: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observed_generation(*, stream: bool = False):
    """生成链路埋点：Span 覆盖完整执行期，流式生成覆盖迭代期。"""

    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, request: AnswerRequest, access: Any):
            attributes = {
                "tenantId": getattr(access, "tenant_id", ""),
                "stream": stream,
                "prompt": self.config.prompt_name,
            }
            if stream:
                iterator = method(self, request, access)

                def observed_iterator():
                    with span("generate.stream", **attributes):
                        yield from iterator

                return observed_iterator()
            with span("generate.answer", **attributes):
                return method(self, request, access)

        return wrapper

    return decorator


class GenerationService:
    """问答编排服务；所有依赖可注入，便于测试与替换供应商。"""

    def __init__(
        self,
        retriever: ClauseRetriever,
        llm: LLMClientManager,
        *,
        templates: PromptTemplateRegistry | None = None,
        validator: InputValidator | None = None,
        grounding: GroundingChecker | None = None,
        formatter: MarkdownFormatter | None = None,
        audit: AuditSink | None = None,
        stats: RefusalStats | None = None,
        config: GenerationConfig | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.config = config or GenerationConfig()
        self.templates = templates or PromptTemplateRegistry.with_builtin_defaults()
        self.validator = validator or InputValidator(
            ValidationPolicy.default(max_chars=self.config.max_input_chars)
        )
        self.grounding = grounding or GroundingChecker(
            mode=self.config.grounding_mode,
            annotation=self.config.unsupported_annotation,
        )
        self.formatter = formatter or MarkdownFormatter(
            max_segment_chars=self.config.max_segment_chars
        )
        self.audit = audit
        self.stats = stats or RefusalStats()

    # ------------------------------------------------------------------ 对外入口

    def precheck(self, request: AnswerRequest, access: Any) -> str:
        """SSE 建立前的同步校验：命中规则即审计并抛 ``InputBlocked``。

        流式接口在返回 200 之前调用，保证非法输入仍以 HTTP 400 拦截并审计；
        ``stream_answer`` 内部会再次校验，重复调用无副作用。
        """
        validation = self.validator.validate(request.query)
        if validation.blocked:
            self._audit_blocked(request, access, validation)
            raise InputBlocked(
                validation.reason or "查询未通过入口校验",
                rule_id=validation.rule_id or "UNKNOWN",
                category=validation.category or "input",
                reason=validation.reason or "INPUT_BLOCKED",
            )
        self._validate_request(request)
        return validation.normalized_query

    @_observed_generation()
    def answer(self, request: AnswerRequest, access: Any) -> Answer:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        warnings: list[str] = []
        prepared = self._prepare(request, access, timings, warnings)

        # 3) 无检索结果：明确拒答并给出查阅原标准指引。
        if not prepared.clauses:
            decision = self.config.refusal.decide(
                has_clauses=False, citation_count=0, confidence=0.0
            )
            return self._finish(
                request,
                access,
                answer_id=prepared.answer_id,
                tenant_id=prepared.tenant_id,
                content=decision.message or "",
                citations=[],
                decision=decision,
                confidence=0.0,
                usage=TokenUsage(),
                finish_reason="REFUSED",
                template_id=None,
                provider=None,
                degraded=prepared.retrieval_degraded,
                warnings=warnings,
                timings=timings,
                started=started,
            )

        # 4) Prompt 组装：超长上下文按窗口策略截断/压缩，不报错。
        template, assembly = self._assemble(prepared.query, prepared.clauses, timings, warnings)

        # 5) LLM 调用：两家供应商直连 + 简单故障切换；全部不可用则降级拒答。
        llm_started = time.perf_counter()
        try:
            response = self.llm.complete(
                [message("system", assembly.system_prompt), message("user", assembly.user_prompt)],
                provider=request.provider,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
        except ModelUnavailable as exc:
            timings["llmMs"] = (time.perf_counter() - llm_started) * 1000
            decision = self.config.refusal.model_unavailable()
            tried = ",".join(item["provider"] for item in exc.attempts) or "unknown"
            warnings.append(f"MODEL_UNAVAILABLE: 全部供应商不可用（已尝试 {tried}）")
            return self._finish(
                request,
                access,
                answer_id=prepared.answer_id,
                tenant_id=prepared.tenant_id,
                content=decision.message or "",
                citations=[],
                decision=decision,
                confidence=0.0,
                usage=TokenUsage(),
                finish_reason="ERROR",
                template_id=template.template_id,
                provider=None,
                degraded=True,
                warnings=warnings,
                timings=timings,
                started=started,
                prompt_describe=assembly.describe(),
            )
        timings["llmMs"] = (time.perf_counter() - llm_started) * 1000
        warnings.extend(response.warnings)

        # 6) 引用校验 + 幻觉抑制 + 置信度 + 拒答判定 + Markdown 格式化。
        post_started = time.perf_counter()
        grounded = self.grounding.check(response.content, assembly.used_clauses)
        confidence = estimate_confidence(assembly.used_clauses, grounded.cited_clause_ids)
        decision = self.config.refusal.decide(
            has_clauses=True,
            citation_count=grounded.citation_count,
            confidence=confidence,
            answer_empty=not grounded.content.strip(),
        )
        warnings.extend(grounded.warnings)
        if decision.refused:
            content = decision.message or ""
            finish_reason = "REFUSED"
            citations = grounded.citations
        else:
            formatted = self.formatter.format(grounded.content)
            content = formatted.content
            finish_reason = "STOP"
            citations = grounded.citations
            warnings.extend(formatted.warnings)
        timings["postprocessMs"] = (time.perf_counter() - post_started) * 1000

        return self._finish(
            request,
            access,
            answer_id=prepared.answer_id,
            tenant_id=prepared.tenant_id,
            content=content,
            citations=citations,
            decision=decision,
            confidence=confidence,
            usage=response.usage,
            finish_reason=finish_reason,
            template_id=template.template_id,
            provider=response.provider,
            degraded=response.degraded or prepared.retrieval_degraded,
            warnings=warnings,
            timings=timings,
            started=started,
            prompt_describe=assembly.describe(),
        )

    @_observed_generation(stream=True)
    def stream_answer(
        self, request: AnswerRequest, access: Any
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """#113 流式问答：``meta`` → ``citation`` → ``delta`` → ``done``。

        流式与最终答案一致：``delta`` 为 LLM 原始增量，``done.content`` 为经过
        #106/#108/#110 后处理的最终正文；客户端收到 ``done`` 后以该正文为准。
        ``meta`` 在生成开始时立即下发；SSE 入口已先同步完成校验，非法输入不会建流。
        """
        started = time.perf_counter()
        timings: dict[str, float] = {}
        warnings: list[str] = []
        # answerId 在进入检索前生成，``meta`` 可立即下发，降低首包等待。
        answer_id = f"ans_{uuid.uuid4().hex}"
        yield (
            "meta",
            {
                "answerId": answer_id,
                "sessionId": request.session_id,
                "traceId": request.trace_id,
                "retrievalMode": "vector",
                "startedAt": _utc_now(),
            },
        )
        prepared = self._prepare(request, access, timings, warnings, answer_id=answer_id)

        if not prepared.clauses:
            decision = self.config.refusal.decide(
                has_clauses=False, citation_count=0, confidence=0.0
            )
            answer = self._finish(
                request,
                access,
                answer_id=prepared.answer_id,
                tenant_id=prepared.tenant_id,
                content=decision.message or "",
                citations=[],
                decision=decision,
                confidence=0.0,
                usage=TokenUsage(),
                finish_reason="REFUSED",
                template_id=None,
                provider=None,
                degraded=prepared.retrieval_degraded,
                warnings=warnings,
                timings=timings,
                started=started,
            )
            yield ("done", self._done_payload(answer))
            return

        template, assembly = self._assemble(prepared.query, prepared.clauses, timings, warnings)
        yield (
            "citation",
            {
                "answerId": prepared.answer_id,
                "citations": [clause.citation().to_dict() for clause in assembly.used_clauses],
            },
        )

        llm_started = time.perf_counter()
        streamed: list[str] = []
        provider: str | None = None
        degraded = prepared.retrieval_degraded
        usage = TokenUsage()
        try:
            for event in self.llm.stream(
                [message("system", assembly.system_prompt), message("user", assembly.user_prompt)],
                provider=request.provider,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            ):
                if event.kind == "meta":
                    provider = event.provider
                    degraded = degraded or event.degraded
                    warnings.extend(event.warnings)
                elif event.kind == "delta":
                    streamed.append(event.delta)
                    if "firstTokenMs" not in timings:
                        timings["firstTokenMs"] = (
                            time.perf_counter() - started
                        ) * 1000
                    yield (
                        "delta",
                        {"answerId": prepared.answer_id, "delta": event.delta},
                    )
                elif event.kind == "end":
                    provider = event.provider or provider
                    usage = event.usage
                    degraded = degraded or event.degraded
                    if event.first_token_ms is not None:
                        timings["llmFirstTokenMs"] = float(event.first_token_ms)
        except ModelUnavailable as exc:
            timings["llmMs"] = (time.perf_counter() - llm_started) * 1000
            decision = self.config.refusal.model_unavailable()
            tried = ",".join(item["provider"] for item in exc.attempts) or "unknown"
            warnings.append(f"MODEL_UNAVAILABLE: 全部供应商不可用（已尝试 {tried}）")
            answer = self._finish(
                request,
                access,
                answer_id=prepared.answer_id,
                tenant_id=prepared.tenant_id,
                content=decision.message or "",
                citations=[],
                decision=decision,
                confidence=0.0,
                usage=TokenUsage(),
                finish_reason="ERROR",
                template_id=template.template_id,
                provider=None,
                degraded=True,
                warnings=warnings,
                timings=timings,
                started=started,
                prompt_describe=assembly.describe(),
            )
            yield ("done", self._done_payload(answer))
            return
        timings["llmMs"] = (time.perf_counter() - llm_started) * 1000

        post_started = time.perf_counter()
        raw_content = "".join(streamed)
        grounded = self.grounding.check(raw_content, assembly.used_clauses)
        confidence = estimate_confidence(assembly.used_clauses, grounded.cited_clause_ids)
        decision = self.config.refusal.decide(
            has_clauses=True,
            citation_count=grounded.citation_count,
            confidence=confidence,
            answer_empty=not grounded.content.strip(),
        )
        warnings.extend(grounded.warnings)
        if decision.refused:
            content = decision.message or ""
            finish_reason = "REFUSED"
        else:
            formatted = self.formatter.format(grounded.content)
            content = formatted.content
            finish_reason = "STOP"
            warnings.extend(formatted.warnings)
        timings["postprocessMs"] = (time.perf_counter() - post_started) * 1000

        answer = self._finish(
            request,
            access,
            answer_id=prepared.answer_id,
            tenant_id=prepared.tenant_id,
            content=content,
            citations=grounded.citations,
            decision=decision,
            confidence=confidence,
            usage=usage,
            finish_reason=finish_reason,
            template_id=template.template_id,
            provider=provider,
            degraded=degraded,
            warnings=warnings,
            timings=timings,
            started=started,
            prompt_describe=assembly.describe(),
        )
        yield ("done", self._done_payload(answer))

    def _prepare(
        self,
        request: AnswerRequest,
        access: Any,
        timings: dict[str, float],
        warnings: list[str],
        *,
        answer_id: str | None = None,
    ) -> "_Prepared":
        """入口校验 + 检索；权限/参数错误上抛，检索异常按无依据降级。"""
        # 1) 入口校验：命中规则即拦截并审计，不进入检索与 LLM。
        validation = self.validator.validate(request.query)
        timings["validationMs"] = validation.latency_ms
        if validation.blocked:
            self._audit_blocked(request, access, validation)
            raise InputBlocked(
                validation.reason or "查询未通过入口校验",
                rule_id=validation.rule_id or "UNKNOWN",
                category=validation.category or "input",
                reason=validation.reason or "INPUT_BLOCKED",
            )
        query = validation.normalized_query
        self._validate_request(request)

        answer_id = answer_id or f"ans_{uuid.uuid4().hex}"
        tenant_id = str(getattr(access, "tenant_id", "default") or "default")

        # 2) 检索：权限/参数错误直接上抛；其它异常按“无依据”降级，不编造。
        retrieval_started = time.perf_counter()
        retrieval_degraded = False
        try:
            clauses = list(
                self.retriever.search(
                    query,
                    access,
                    top_k=int(request.top_k),
                    kb_id=request.kb_id,
                    kb_ids=list(request.kb_ids),
                    filters=request.filters,
                    trace_id=request.trace_id,
                )
            )
        except Exception as exc:
            if type(exc).__name__ in {"PermissionDenied", "InvalidRequest"}:
                raise
            retrieval_degraded = True
            clauses = []
            warnings.append(
                f"RETRIEVAL_DEGRADED: 检索服务异常（{type(exc).__name__}），已按无依据处理"
            )
            logger.warning("检索异常，按无依据处理 traceId=%s", request.trace_id, exc_info=True)
        timings["retrievalMs"] = (time.perf_counter() - retrieval_started) * 1000
        return _Prepared(
            query=query,
            answer_id=answer_id,
            tenant_id=tenant_id,
            clauses=clauses,
            retrieval_degraded=retrieval_degraded,
        )

    def _assemble(
        self,
        query: str,
        clauses: list[RetrievedClause],
        timings: dict[str, float],
        warnings: list[str],
    ):
        prompt_started = time.perf_counter()
        template = self.templates.get(self.config.prompt_name, self.config.prompt_version)
        assembly = assemble_prompt(
            template,
            clauses,
            query,
            policy=ContextWindowPolicy(
                max_context_chars=self.config.max_context_chars,
                max_clauses=self.config.max_clauses_in_prompt,
            ),
        )
        timings["promptMs"] = (time.perf_counter() - prompt_started) * 1000
        warnings.extend(assembly.warnings)
        return template, assembly

    @staticmethod
    def _done_payload(answer: Answer) -> dict[str, Any]:
        return {
            "answerId": answer.answer_id,
            "finishReason": answer.finish_reason,
            "refused": answer.refused,
            "refusalReason": answer.refusal_reason,
            "citations": [citation.to_dict() for citation in answer.citations],
            "usage": answer.usage.to_dict(),
            "confidence": round(float(answer.confidence), 4),
            "provider": answer.provider,
            "degraded": answer.degraded,
            "warnings": list(answer.warnings),
            "timings": {key: round(value, 3) for key, value in answer.timings.items()},
            # 客户端以该正文覆盖流式增量，确保“流式与最终答案一致”。
            "content": answer.content,
            "format": answer.format,
            "promptTemplateVersion": answer.prompt_template_version,
            "traceId": answer.trace_id,
        }

    def prompt_templates(self) -> list[dict[str, Any]]:
        return self.templates.list_templates()

    def prompt_versions(self, name: str) -> list[dict[str, Any]]:
        return self.templates.list_versions(name)

    def refusal_stats(self) -> dict[str, Any]:
        return self.stats.snapshot()

    # ------------------------------------------------------------------ 内部

    def _validate_request(self, request: AnswerRequest) -> None:
        if request.kb_id and request.kb_ids:
            raise InvalidInput("kbId 与 kbIds 互斥，不能同时传入")
        top_k = int(request.top_k)
        if not 1 <= top_k <= self.config.max_top_k:
            raise InvalidInput(f"topK 必须在 1..{self.config.max_top_k} 之间")
        if len(request.kb_ids) > 20:
            raise InvalidInput("kbIds 最多 20 个")

    def _finish(
        self,
        request: AnswerRequest,
        access: Any,
        *,
        answer_id: str,
        tenant_id: str,
        content: str,
        citations: list[Any],
        decision: RefusalDecision,
        confidence: float,
        usage: TokenUsage,
        finish_reason: str,
        template_id: str | None,
        provider: str | None,
        degraded: bool,
        warnings: Sequence[str],
        timings: dict[str, float],
        started: float,
        prompt_describe: dict[str, Any] | None = None,
    ) -> Answer:
        timings["totalMs"] = (time.perf_counter() - started) * 1000
        if decision.refused:
            self.stats.record_refusal(decision.reason_code or "UNKNOWN", tenant_id=tenant_id)
            mark_generation(
                True,
                timings["totalMs"] / 1000,
                service="svc-query",
                reason=decision.reason_code or "UNKNOWN",
            )
        else:
            self.stats.record_answer()
            mark_generation(False, timings["totalMs"] / 1000, service="svc-query")
        answer = Answer(
            answer_id=answer_id,
            tenant_id=tenant_id,
            session_id=request.session_id,
            content=content,
            citations=list(citations),
            refused=decision.refused,
            refusal_reason=decision.reason_text if decision.refused else None,
            confidence=confidence,
            usage=usage,
            finish_reason=finish_reason,
            format=self.config.answer_format,
            prompt_template_version=template_id,
            provider=provider,
            degraded=degraded,
            warnings=list(warnings),
            timings=timings,
            trace_id=request.trace_id,
        )
        self._audit_answer(request, access, answer, prompt_describe)
        return answer

    def _audit_answer(
        self,
        request: AnswerRequest,
        access: Any,
        answer: Answer,
        prompt_describe: dict[str, Any] | None,
    ) -> None:
        detail: dict[str, Any] = {
            "answerId": answer.answer_id,
            "refused": answer.refused,
            "refusalReason": answer.refusal_reason,
            "confidence": answer.confidence,
            "citationCount": len(answer.citations),
            "provider": answer.provider,
            "promptTemplateVersion": answer.prompt_template_version,
            "inputChars": len(request.query or ""),
            "degraded": answer.degraded,
            "latencyMs": round(answer.timings.get("totalMs", 0.0), 3),
            "usage": answer.usage.to_dict(),
        }
        if prompt_describe:
            # 指纹/长度可追溯，但不包含 Prompt 正文。
            detail["prompt"] = prompt_describe
        safe_record(
            self.audit,
            audit_entry(
                ACTION_CHAT_QUERY,
                user_id=str(getattr(access, "user_id", "anonymous")),
                tenant_id=answer.tenant_id,
                allowed=True,
                reason=answer.refusal_reason,
                trace_id=request.trace_id,
                detail=detail,
            ),
        )

    def _audit_blocked(
        self, request: AnswerRequest, access: Any, validation: ValidationResult
    ) -> None:
        action = (
            ACTION_INJECTION_BLOCKED
            if validation.category == CATEGORY_PROMPT_INJECTION
            else ACTION_INPUT_BLOCKED
        )
        safe_record(
            self.audit,
            audit_entry(
                action,
                user_id=str(getattr(access, "user_id", "anonymous")),
                tenant_id=str(getattr(access, "tenant_id", "default")),
                allowed=False,
                reason=validation.rule_id,
                trace_id=request.trace_id,
                detail={
                    "ruleId": validation.rule_id,
                    "category": validation.category,
                    "reason": validation.reason,
                    "matchedText": validation.matched_text,
                    "queryChars": validation.original_length,
                },
            ),
        )


__all__ = [
    "ClauseRetriever",
    "GenerationConfig",
    "GenerationService",
]
