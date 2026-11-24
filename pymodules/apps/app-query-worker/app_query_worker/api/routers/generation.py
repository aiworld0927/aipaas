"""生成链路接口：#98 LLM 调用、#103 Prompt 模板、#106/#108 引用与拒答、
#110 格式化、#114 入口校验。

路径约定：
- 内部 LLM 调用固定为 ``POST /internal/llm/chat/completions``；
- ``POST /models/chat`` 为 OpenAPI 契约别名（同一实现）；
- ``POST /api/v1/chat`` 与 ``POST /api/v1/chat/stream`` 供 Java 编排层直连（#407/#113）；
- 对外 OpenAI 兼容 ``/v1/chat/completions`` 归 P1 #411，本服务不注册该路径。

日志与响应不输出完整 Prompt；供应商密钥不进入日志与错误体。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from aipaas_common.core.log import logger
from aipaas_generation import (
    ACTION_MODEL_INVOKE,
    AnswerRequest,
    AuditSink,
    GenerationService,
    InputBlocked,
    InvalidInput,
    LLMClientManager,
    ModelUnavailable,
    TemplateNotFound,
    audit_entry,
    safe_record,
    validate_messages,
)
from aipaas_retrieval import PermissionDenied
from app_query_worker.api.generation_stream import (
    StreamRegistry,
    format_ping,
    format_sse,
    parse_last_event_id,
)
from app_query_worker.api.routers.retrieval import FiltersIn, access_from_headers

router = APIRouter(tags=["生成与推理"])

HEADER_TRACE_ID = "X-Trace-Id"
HEADER_LLM_PROVIDER = "X-LLM-Provider"
QUERY_HARD_LIMIT = 20000


def get_generation_service_dep() -> GenerationService:
    """延迟导入依赖工厂：避免导入路由时加载 app-query-worker 配置（测试可覆写）。"""
    from app_query_worker.api.dependencies import get_generation_service

    return get_generation_service()


def get_llm_manager_dep() -> LLMClientManager:
    from app_query_worker.api.dependencies import get_llm_manager

    return get_llm_manager()


def get_generation_audit_sink_dep() -> AuditSink:
    from app_query_worker.api.dependencies import get_generation_audit_sink

    return get_generation_audit_sink()


def get_stream_registry_dep() -> StreamRegistry:
    from app_query_worker.api.dependencies import get_generation_stream_registry

    return get_generation_stream_registry()


GenerationServiceDep = Annotated[GenerationService, Depends(get_generation_service_dep)]
LLMManagerDep = Annotated[LLMClientManager, Depends(get_llm_manager_dep)]
GenerationAuditDep = Annotated[AuditSink, Depends(get_generation_audit_sink_dep)]
StreamRegistryDep = Annotated[StreamRegistry, Depends(get_stream_registry_dep)]


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str = ""
    content: str = ""


class ChatCompletionIn(BaseModel):
    """内部 LLM 调用入参；``provider`` 用于手动切换供应商。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    provider: str | None = None
    model: str | None = None
    messages: list[MessageIn] = Field(default_factory=list)
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, alias="maxTokens", ge=1, le=8192)


class ChatIn(BaseModel):
    """与 OpenAPI ``ChatRequest`` 对齐；P1 多轮/重排字段接收但不生效。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    query: str = Field("", max_length=QUERY_HARD_LIMIT)
    kb_id: str | None = Field(None, alias="kbId")
    kb_ids: list[str] = Field(default_factory=list, alias="kbIds")
    session_id: str | None = Field(None, alias="sessionId")
    conversation_mode: str = Field("SINGLE_TURN", alias="conversationMode")
    retrieval_mode: str = Field("vector", alias="retrievalMode")
    rerank: bool = False
    top_k: int = Field(5, alias="topK", ge=1, le=20)
    capability: str = "RAG"
    filters: FiltersIn = Field(default_factory=FiltersIn)

    def to_model(self, trace_id: str, provider: str | None) -> AnswerRequest:
        return AnswerRequest(
            query=self.query,
            kb_id=self.kb_id,
            kb_ids=list(self.kb_ids),
            top_k=self.top_k,
            filters=self.filters.to_model(),
            session_id=self.session_id,
            provider=provider,
            trace_id=trace_id,
        )


def _trace_id(request: Request) -> str:
    return request.headers.get(HEADER_TRACE_ID) or f"gen_{uuid.uuid4().hex}"


def _ok(data: dict, trace_id: str) -> dict:
    return {"code": "OK", "message": "OK", "data": data, "traceId": trace_id}


def _error(
    status: int, code: str, message: str, trace_id: str, data: dict | None = None
) -> JSONResponse:
    payload = {"reason": message, "retryable": status >= 500, **(data or {})}
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "data": payload, "traceId": trace_id},
    )


def _audit_model(
    audit: AuditSink,
    request: Request,
    trace_id: str,
    *,
    provider: str | None,
    model: str | None,
    message_count: int,
    prompt_chars: int,
    ok: bool,
    latency_ms: int,
    error_code: str | None = None,
) -> None:
    access = access_from_headers(request.headers)
    safe_record(
        audit,
        audit_entry(
            ACTION_MODEL_INVOKE,
            user_id=access.user_id,
            tenant_id=access.tenant_id,
            allowed=ok,
            reason=error_code,
            trace_id=trace_id,
            detail={
                "provider": provider,
                "model": model,
                "messages": message_count,
                "promptChars": prompt_chars,
                "latencyMs": latency_ms,
                "errorCode": error_code,
            },
        ),
    )


async def _llm_chat(
    payload: ChatCompletionIn, request: Request, manager: LLMClientManager, audit: AuditSink
) -> JSONResponse | dict:
    trace_id = _trace_id(request)
    try:
        messages = validate_messages([item.model_dump() for item in payload.messages])
    except ValueError as exc:
        return _error(400, "PARAM_INVALID", str(exc), trace_id)

    if payload.provider:
        try:
            manager.switch(payload.provider)
        except ModelUnavailable as exc:
            return _error(400, "PARAM_INVALID", str(exc), trace_id)

    prompt_chars = sum(len(item["content"]) for item in messages)
    try:
        response = await asyncio.to_thread(
            manager.complete,
            messages,
            provider=payload.provider,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    except ModelUnavailable as exc:
        _audit_model(
            audit,
            request,
            trace_id,
            provider=payload.provider,
            model=payload.model,
            message_count=len(messages),
            prompt_chars=prompt_chars,
            ok=False,
            latency_ms=0,
            error_code="MODEL_UNAVAILABLE",
        )
        return _error(
            503,
            "MODEL_UNAVAILABLE",
            str(exc),
            trace_id,
            {"attempts": exc.attempts, "retryable": exc.retryable},
        )
    except Exception:
        logger.exception("LLM 调用异常 traceId={}", trace_id)
        _audit_model(
            audit,
            request,
            trace_id,
            provider=payload.provider,
            model=payload.model,
            message_count=len(messages),
            prompt_chars=prompt_chars,
            ok=False,
            latency_ms=0,
            error_code="INTERNAL_ERROR",
        )
        return _error(500, "INTERNAL_ERROR", "LLM 调用失败", trace_id)

    _audit_model(
        audit,
        request,
        trace_id,
        provider=response.provider,
        model=response.model,
        message_count=len(messages),
        prompt_chars=prompt_chars,
        ok=True,
        latency_ms=response.latency_ms,
    )
    return _ok(
        {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
            "usage": response.usage.to_dict(),
            "latencyMs": response.latency_ms,
            "degraded": response.degraded,
            "warnings": list(response.warnings),
            "attempts": list(response.attempts),
        },
        trace_id,
    )


@router.post("/internal/llm/chat/completions")
async def internal_chat_completion(
    payload: ChatCompletionIn, request: Request, manager: LLMManagerDep, audit: GenerationAuditDep
):
    """#98 内部 LLM 通道：双供应商直连、手动切换与故障切换。"""
    return await _llm_chat(payload, request, manager, audit)


@router.post("/models/chat")
async def models_chat(
    payload: ChatCompletionIn, request: Request, manager: LLMManagerDep, audit: GenerationAuditDep
):
    """OpenAPI ``/models/chat`` 契约别名，与内部通道共用实现、不共用对外路径。"""
    return await _llm_chat(payload, request, manager, audit)


@router.get("/models/providers")
async def list_model_providers(request: Request, manager: LLMManagerDep):
    return _ok({"items": manager.registry.items()}, _trace_id(request))


@router.get("/models/health")
async def model_health(request: Request, manager: LLMManagerDep):
    return _ok(manager.health(), _trace_id(request))


@router.post("/api/v1/chat")
@router.post("/chat")
async def chat(
    payload: ChatIn, request: Request, service: GenerationServiceDep
):
    """金融标准问答（非流式）：校验 → 检索 → Prompt → LLM → 引用/拒答 → Markdown。"""
    trace_id = _trace_id(request)
    access = access_from_headers(request.headers)
    if not access.has_permission("chat:use"):
        return _error(403, "FORBIDDEN", "缺少问答权限 chat:use", trace_id)
    if (payload.capability or "RAG").upper() != "RAG":
        return _error(400, "PARAM_INVALID", "P0 仅支持 RAG 能力", trace_id)

    provider = request.headers.get(HEADER_LLM_PROVIDER)
    try:
        answer = await asyncio.to_thread(
            service.answer, payload.to_model(trace_id, provider), access
        )
    except InputBlocked as exc:
        return _error(400, "INPUT_BLOCKED", str(exc), trace_id, exc.to_dict())
    except InvalidInput as exc:
        return _error(400, "PARAM_INVALID", str(exc), trace_id)
    except PermissionDenied as exc:
        return _error(403, "FORBIDDEN", str(exc), trace_id)
    except Exception:
        logger.exception("问答生成失败 traceId={}", trace_id)
        return _error(500, "INTERNAL_ERROR", "问答生成失败", trace_id)
    return _ok(answer.to_dict(), trace_id)


@router.post("/api/v1/chat/stream")
@router.post("/chat/stream")
async def chat_stream(
    payload: ChatIn,
    request: Request,
    service: GenerationServiceDep,
    registry: StreamRegistryDep,
):
    """#113 SSE 流式问答：meta / citation / delta / done / error。

    客户端断线后带 ``Last-Event-ID: <streamKey>:<seq>`` 重连即可从断点续传；
    缓冲不存在（进程重启/过期）时重新生成并从头推送，保证最终结果完整。
    """
    trace_id = _trace_id(request)
    access = access_from_headers(request.headers)
    if not access.has_permission("chat:use"):
        return _error(403, "FORBIDDEN", "缺少问答权限 chat:use", trace_id)
    if (payload.capability or "RAG").upper() != "RAG":
        return _error(400, "PARAM_INVALID", "P0 仅支持 RAG 能力", trace_id)

    provider = request.headers.get(HEADER_LLM_PROVIDER)
    answer_request = payload.to_model(trace_id, provider)

    # 建立 SSE 前同步校验：非法输入仍以 400 返回并写审计，避免 200 流内报错。
    try:
        service.precheck(answer_request, access)
    except InputBlocked as exc:
        return _error(400, "INPUT_BLOCKED", str(exc), trace_id, exc.to_dict())
    except InvalidInput as exc:
        return _error(400, "PARAM_INVALID", str(exc), trace_id)
    except PermissionDenied as exc:
        return _error(403, "FORBIDDEN", str(exc), trace_id)

    last_event_id = request.headers.get("Last-Event-ID") or request.headers.get("last-event-id")
    key, cursor = parse_last_event_id(last_event_id)
    buffer = registry.get(key) if key else None
    if buffer is None:
        buffer = registry.start(lambda: service.stream_answer(answer_request, access))
        cursor = 0

    async def event_source():
        nonlocal cursor
        while True:
            pending, finished = await asyncio.to_thread(buffer.wait, cursor, 15.0)
            for item in pending:
                cursor = item.seq
                yield format_sse(item, buffer.key)
            if finished:
                return
            if not pending:
                yield format_ping()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            HEADER_TRACE_ID: trace_id,
        },
    )


@router.get("/prompts/templates")
async def list_prompt_templates(request: Request, service: GenerationServiceDep):
    return _ok({"items": service.prompt_templates()}, _trace_id(request))


@router.get("/prompts/templates/{template_name}/versions")
async def list_prompt_template_versions(
    template_name: str, request: Request, service: GenerationServiceDep
):
    try:
        items = service.prompt_versions(template_name)
    except TemplateNotFound as exc:
        return _error(404, "NOT_FOUND", str(exc), _trace_id(request))
    return _ok({"items": items}, _trace_id(request))


@router.get("/internal/generation/stats")
async def generation_stats(request: Request, service: GenerationServiceDep):
    """拒答统计（内部）：按原因/租户累计，用于验收“拒答可统计”。"""
    return _ok(service.refusal_stats(), _trace_id(request))
