"""#405 标准简介生成接口：抽取五要素、缺失标注“未识别”、版本化可追溯。"""
from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from aipaas_pipeline.summary import SummaryForbidden, SummaryNotFound
from app_ingest_worker.api.dependencies import SummaryServiceDep

router = APIRouter(prefix="/api/v1", tags=["生成与推理"])


class SummaryCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    regenerate: bool = False
    tenant_id: str | None = None


@router.post(
    "/documents/{document_id}/summaries",
    summary="标准简介自动生成（P0 #405）",
)
async def generate_summary(
    document_id: str,
    service: SummaryServiceDep,
    request: SummaryCreateRequest | None = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """从条目化条款与标准元数据抽取五要素；缺失要素返回“未识别”。"""
    body = request or SummaryCreateRequest()
    tenant_id = body.tenant_id or x_tenant_id
    try:
        payload = service.generate(
            document_id,
            tenant_id=tenant_id,
            regenerate=body.regenerate,
        )
    except SummaryNotFound as exc:
        return JSONResponse(status_code=404, content={"code": 404, "message": str(exc), "data": None})
    except SummaryForbidden as exc:
        return JSONResponse(status_code=403, content={"code": 403, "message": str(exc), "data": None})
    return {"code": 200, "message": "ok", "data": payload}


@router.get(
    "/documents/{document_id}/summaries/latest",
    summary="最新标准简介",
)
async def latest_summary(
    document_id: str,
    service: SummaryServiceDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    try:
        payload = service.latest(document_id, tenant_id=x_tenant_id)
    except SummaryNotFound as exc:
        return JSONResponse(status_code=404, content={"code": 404, "message": str(exc), "data": None})
    except SummaryForbidden as exc:
        return JSONResponse(status_code=403, content={"code": 403, "message": str(exc), "data": None})
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "标准简介不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}
