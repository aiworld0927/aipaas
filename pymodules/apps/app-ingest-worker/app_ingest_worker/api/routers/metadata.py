"""#57 标准元数据接口：查询、人工修正、过滤与审计。

六要素中的条款编号随 chunk 输出（``chunk.clauseNo`` + ``chunk.meta.clauseNo``），
文档级五要素在此接口返回；``securityLevel/domain/effectiveAt`` 原样返回但不参与 P0 过滤。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from aipaas_common.core.log import logger
from aipaas_pipeline.annotation import MetaFilter
from app_ingest_worker.api.dependencies import ParseServiceDep

router = APIRouter(prefix="/api/v1", tags=["知识构建与索引"])


class MetadataUpdateRequest(BaseModel):
    """人工修正元数据：只允许契约声明的可编辑字段。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    standard_no: str | None = Field(None, alias="standardNo")
    standard_name: str | None = Field(None, alias="standardName")
    issuer: str | None = None
    issued_at: str | None = Field(None, alias="issuedAt")
    effective_at: str | None = Field(None, alias="effectiveAt")
    replaced_by: str | None = Field(None, alias="replacedBy")
    domain: str | None = None
    security_level: str | None = Field(None, alias="securityLevel")
    confidence: float | None = None

    def patch(self) -> dict:
        return {key: value for key, value in self.model_dump(by_alias=True).items() if value is not None}


@router.get("/documents/{document_id}/metadata", summary="文档标准元数据（六要素）")
async def get_metadata(document_id: str, service: ParseServiceDep):
    meta = service.get_metadata(document_id)
    if meta is None:
        return JSONResponse(status_code=404, content={"code": 404, "message": "元数据不存在", "data": None})
    return {"code": 200, "message": "ok", "data": meta}


@router.put("/documents/{document_id}/metadata", summary="人工修正标准元数据（可审计）")
async def update_metadata(
    document_id: str,
    request: MetadataUpdateRequest,
    http_request: Request,
    service: ParseServiceDep,
):
    actor = http_request.headers.get("X-User-Id") or "api"
    try:
        meta = service.update_metadata(document_id, request.patch(), actor=actor)
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "元数据不存在", "data": None})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(exc), "data": None})
    # 人工修正后需要重建索引/重新标注 chunk，P0 不自动触发，由后续任务显式发起。
    logger.info("元数据人工修正 document=%s actor=%s fields=%s", document_id, actor, list(request.patch()))
    return {"code": 200, "message": "ok", "data": meta}


@router.get("/documents/{document_id}/metadata/audit", summary="元数据标注/修正审计")
async def metadata_audit(document_id: str, service: ParseServiceDep):
    return {"code": 200, "message": "ok", "data": {"documentId": document_id, "entries": service.metadata_audit(document_id)}}


@router.get("/metadata:query", summary="按标准号/发布机构/实施时间过滤元数据")
async def query_metadata(
    service: ParseServiceDep,
    standard_no: str | None = Query(None, alias="standardNo"),
    issuer: str | None = None,
    effective_from: str | None = Query(None, alias="effectiveFrom"),
    effective_to: str | None = Query(None, alias="effectiveTo"),
    keyword: str | None = None,
    domain: str | None = None,
    limit: int = Query(50, ge=1, le=500),
):
    meta_filter = MetaFilter(
        standard_no=standard_no,
        issuer=issuer,
        effective_from=effective_from,
        effective_to=effective_to,
        keyword=keyword,
        domain=domain,
    )
    items = service.query_metadata(meta_filter)
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "filter": meta_filter.to_dict(),
            "total": len(items),
            "items": items[:limit],
            "truncated": len(items) > limit,
        },
    }
