"""检索引擎接口：``/retrieve``（#70 召回 + #88 权限）与 ``/retrieve/query-understanding``（#63）。

权限快照由网关（Java RBAC）以请求头注入，本服务不缓存角色/知识库授权：
``X-User-Id``、``X-Tenant-Id``、``X-Roles``、``X-Permissions``、``X-Kb-Ids``、``X-Security-Level``。
每次请求实时解析，因此授权变更下一请求立即生效。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Mapping

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from aipaas_common.core.log import logger
from aipaas_retrieval import (
    AccessContext,
    InvalidRequest,
    PermissionDenied,
    RetrievalFilters,
    RetrievalService,
    RetrieveRequest,
    understand_query,
)

router = APIRouter(tags=["检索引擎"])


def get_retrieval_service_dep() -> RetrievalService:
    """延迟导入依赖工厂：避免导入路由时触发 app-query-worker 配置加载（测试可覆写）。"""
    from app_query_worker.api.dependencies import get_retrieval_service

    return get_retrieval_service()


RetrievalServiceDep = Annotated[RetrievalService, Depends(get_retrieval_service_dep)]

HEADER_USER_ID = "X-User-Id"
HEADER_TENANT_ID = "X-Tenant-Id"
HEADER_ROLES = "X-Roles"
HEADER_PERMISSIONS = "X-Permissions"
HEADER_KB_IDS = "X-Kb-Ids"
HEADER_SECURITY_LEVEL = "X-Security-Level"
HEADER_TRACE_ID = "X-Trace-Id"

#: 查询硬上限（防止超长请求拖垮服务）；业务截断阈值由检索服务按 2000 字符执行。
QUERY_HARD_LIMIT = 20000


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def access_from_headers(headers: Mapping[str, str]) -> AccessContext:
    """由网关注入的请求头构造权限快照（实时解析，不缓存）。"""
    permissions = frozenset(_split(headers.get(HEADER_PERMISSIONS)))
    kb_ids = _split(headers.get(HEADER_KB_IDS))
    kb_permissions = {kb: frozenset({"retrieval:query"}) for kb in kb_ids}
    return AccessContext(
        user_id=headers.get(HEADER_USER_ID) or "anonymous",
        tenant_id=headers.get(HEADER_TENANT_ID) or "default",
        roles=tuple(_split(headers.get(HEADER_ROLES))),
        permissions=permissions,
        kb_permissions=kb_permissions,
        all_kbs="*" in permissions,
        max_security_level=(headers.get(HEADER_SECURITY_LEVEL) or "PUBLIC").upper(),
    )


class FiltersIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    standard_no: str | None = Field(None, alias="standardNo")
    issuer: str | None = None
    clause_no: str | None = Field(None, alias="clauseNo")
    domain: str | None = None
    security_level: str | None = Field(None, alias="securityLevel")
    effective_from: str | None = Field(None, alias="effectiveFrom")
    effective_to: str | None = Field(None, alias="effectiveTo")

    def to_model(self) -> RetrievalFilters:
        return RetrievalFilters(
            standard_no=self.standard_no,
            issuer=self.issuer,
            clause_no=self.clause_no,
            domain=self.domain,
            security_level=self.security_level,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
        )


class RetrieveIn(BaseModel):
    """与 OpenAPI ``RetrieveRequest`` 对齐；P1/P2 字段先接收、不生效。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    query: str = Field("", max_length=QUERY_HARD_LIMIT)
    kb_id: str | None = Field(None, alias="kbId")
    kb_ids: list[str] = Field(default_factory=list, alias="kbIds")
    top_k: int = Field(5, alias="topK", ge=1, le=50)
    retrieval_mode: str = Field("vector", alias="retrievalMode")
    rerank: bool = False
    session_id: str | None = Field(None, alias="sessionId")
    filters: FiltersIn = Field(default_factory=FiltersIn)

    def to_model(self, trace_id: str) -> RetrieveRequest:
        return RetrieveRequest(
            query=self.query,
            kb_id=self.kb_id,
            kb_ids=list(self.kb_ids),
            top_k=self.top_k,
            retrieval_mode=self.retrieval_mode,
            rerank=self.rerank,
            session_id=self.session_id,
            filters=self.filters.to_model(),
            trace_id=trace_id,
        )


class UnderstandIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    query: str = Field("", max_length=QUERY_HARD_LIMIT)
    kb_id: str | None = Field(None, alias="kbId")


def _trace_id(request: Request) -> str:
    return request.headers.get(HEADER_TRACE_ID) or f"query_{uuid.uuid4().hex}"


def _ok(data: dict, trace_id: str) -> dict:
    return {"code": "OK", "message": "OK", "data": data, "traceId": trace_id}


def _error(status: int, code: str, message: str, trace_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "data": {"reason": message, "retryable": status >= 500},
            "traceId": trace_id,
        },
    )


@router.post("/retrieve")
async def retrieve(payload: RetrieveIn, request: Request, service: RetrievalServiceDep):
    """向量语义召回；越权/密级不符条款在召回阶段即被过滤，0 泄露。"""
    trace_id = _trace_id(request)
    access = access_from_headers(request.headers)
    try:
        result = await asyncio.to_thread(service.retrieve, payload.to_model(trace_id), access)
    except InvalidRequest as exc:
        return _error(400, "PARAM_INVALID", str(exc), trace_id)
    except PermissionDenied as exc:
        return _error(403, "FORBIDDEN", str(exc), trace_id)
    except Exception:
        logger.exception("检索失败 traceId={}", trace_id)
        return _error(500, "INTERNAL_ERROR", "检索失败", trace_id)
    return _ok(result.to_dict(), trace_id)


@router.post("/retrieve/query-understanding")
async def query_understanding(payload: UnderstandIn, request: Request):
    """查询归一与清洗预览：标准编号/条款编号归一、分词与兜底告警。"""
    trace_id = _trace_id(request)
    understanding = understand_query(payload.query)
    return _ok(understanding.to_dict(), trace_id)
