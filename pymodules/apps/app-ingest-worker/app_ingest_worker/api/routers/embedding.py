"""#44 批量向量化任务接口：创建、进度、暂停、恢复、失败重试与维度校验。"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from aipaas_common.core.log import logger
from aipaas_common.core.observability import span
from app_ingest_worker.api.dependencies import EmbeddingServiceDep, ParseServiceDep

router = APIRouter(prefix="/api/v1", tags=["知识构建与索引"])


class CreateEmbeddingJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_id: str = Field(..., alias="kbId")
    document_ids: list[str] = Field(default_factory=list, alias="documentIds")
    model: str | None = None


def _collect_chunks(service, document_ids: list[str]) -> tuple[list[dict], list[str]]:
    """汇总待向量化 chunk；documentIds 为空表示当前租户全部已解析文档。"""
    records = {record.document_id: record for record in service.list_parse_records()}
    targets = document_ids or [doc_id for doc_id, record in records.items() if record.success]
    chunks: list[dict] = []
    resolved: list[str] = []
    for doc_id in targets:
        artifact = service.get_chunks_artifact(doc_id)
        if not artifact:
            continue
        items = artifact.get("chunks") or []
        if items:
            chunks.extend(items)
            resolved.append(doc_id)
    return chunks, resolved


@router.post("/index/embedding-jobs", status_code=202, summary="创建批量向量化任务")
async def create_job(
    request: CreateEmbeddingJobRequest,
    http_request: Request,
    background: BackgroundTasks,
    service: ParseServiceDep,
    embeddings: EmbeddingServiceDep,
):
    idempotency_key = http_request.headers.get("Idempotency-Key")
    chunks, resolved = _collect_chunks(service, request.document_ids)
    if request.document_ids and not resolved:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "未找到可分块/向量化的文档", "data": None},
        )
    try:
        with span("index.embedding.submit", kbId=request.kb_id, chunkCount=len(chunks)):
            job = embeddings.create_job(
                request.kb_id,
                chunks,
                document_ids=resolved,
                idempotency_key=idempotency_key,
                model=request.model,
            )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(exc), "data": None})
    background.add_task(embeddings.run, job.job_id)
    logger.info("创建向量化任务 job=%s kb=%s chunks=%s", job.job_id, request.kb_id, job.total)
    return {"code": 202, "message": "已创建", "data": job.to_dict()}


@router.get("/index/embedding-jobs", summary="向量化任务列表")
async def list_jobs(embeddings: EmbeddingServiceDep):
    return {"code": 200, "message": "ok", "data": {"items": [job.to_dict() for job in embeddings.list_jobs()]}}


@router.get("/index/embedding-jobs/{job_id}", summary="向量化任务进度与失败定位")
async def get_job(job_id: str, embeddings: EmbeddingServiceDep):
    try:
        job = embeddings.get_job(job_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "任务不存在", "data": None})
    return {"code": 200, "message": "ok", "data": job.to_dict()}


@router.post("/index/embedding-jobs/{job_id}:pause", summary="暂停向量化任务")
async def pause_job(job_id: str, embeddings: EmbeddingServiceDep):
    try:
        job = embeddings.pause(job_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "任务不存在", "data": None})
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"code": 409, "message": str(exc), "data": None})
    return {"code": 200, "message": "ok", "data": job.to_dict()}


@router.post("/index/embedding-jobs/{job_id}:resume", summary="恢复向量化任务")
async def resume_job(job_id: str, embeddings: EmbeddingServiceDep):
    try:
        job = embeddings.resume(job_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "任务不存在", "data": None})
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"code": 409, "message": str(exc), "data": None})
    return {"code": 200, "message": "ok", "data": job.to_dict()}


@router.post("/index/embedding-jobs/{job_id}:retry", summary="重试失败 chunk")
async def retry_failed(job_id: str, embeddings: EmbeddingServiceDep):
    try:
        current = embeddings.get_job(job_id)
        if current.status == "RUNNING":
            embeddings.pause(job_id)
        job = embeddings.retry_failures(job_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "任务不存在", "data": None})
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"code": 409, "message": str(exc), "data": None})
    return {"code": 200, "message": "ok", "data": job.to_dict()}


@router.get("/index/embedding-jobs/{job_id}/vectors:verify", summary="校验向量维度与模型一致性")
async def verify_vectors(job_id: str, embeddings: EmbeddingServiceDep):
    try:
        result = embeddings.verify_vectors(job_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"code": 404, "message": "任务不存在", "data": None})
    return {"code": 200, "message": "ok", "data": result}
