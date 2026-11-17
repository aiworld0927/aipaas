"""健康与模型信息接口。"""
from fastapi import APIRouter, HTTPException

from model_embedding_worker import service as backend

router = APIRouter(tags=["健康检查"])


@router.get("/health", summary="存活探针")
async def health():
    """仅表示进程存活，不检查模型状态（供容器 liveness 探针使用）。"""
    return {"status": "ok", "service": "model-embedding-worker"}


@router.get("/ready", summary="就绪探针")
async def ready():
    """后端就绪返回 200，否则 503（供容器 readiness 探针使用）。"""
    if not backend.is_ready():
        raise HTTPException(status_code=503, detail="嵌入服务尚未就绪")
    return {"status": "ready", **backend.info()}


@router.get("/v1/model/info", summary="模型信息")
async def model_info():
    """返回当前后端、模型、设备、维度等信息。"""
    return {**backend.info(), "loaded": backend.is_ready()}
