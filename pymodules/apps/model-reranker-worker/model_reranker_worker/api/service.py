"""重排序模型服务的 HTTP 接口定义（全异步）。

启动方式：
    uvicorn model_reranker_worker.api.service:app --host 0.0.0.0 --port 8004
"""
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from aipaas_common.conf.settings import load_settings

# 配置引导：必须在导入业务模块前完成加载（唯一权威来源，失败即终止启动）
settings = load_settings("model_reranker_worker")

from aipaas_common.core.log import logger
from model_reranker_worker import service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动时完成就绪准备（加载本地模型或校验在线配置）。

    失败不中断进程：/ready 返回 503 暴露原因。
    """
    try:
        await service.ensure_ready()
    except Exception as exc:
        logger.error("重排序服务启动准备失败，/ready 将返回 503：{}", exc, exc_info=True)
    yield


app = FastAPI(
    title="bge-reranker-large 重排序服务",
    description="AIPaaS 重排序模型独立推理服务，支持离线（本地模型）与在线（DashScope）两种后端",
    lifespan=lifespan,
)


class RerankRequest(BaseModel):
    """重排序请求体。"""

    query: str = Field(..., description="查询文本")
    documents: list[str] = Field(..., description="候选文档列表")


class RerankResponse(BaseModel):
    """重排序响应体，scores 与 documents 顺序一一对应。"""

    scores: list[float] = Field(..., description="每个候选文档的相关性分数")


@app.get("/health", summary="存活探针")
async def health():
    """仅表示进程存活，不检查模型状态（供容器 liveness 探针使用）。"""
    return {"status": "ok", "service": "model-reranker-worker"}


@app.get("/ready", summary="就绪探针")
async def ready():
    """后端就绪返回 200，否则 503（供容器 readiness 探针使用）。"""
    if not service.is_ready():
        raise HTTPException(status_code=503, detail="重排序服务尚未就绪")
    return {"status": "ready", **service.info()}


@app.get("/v1/model/info", summary="模型信息")
async def model_info():
    """返回当前后端、模型、设备等信息。"""
    return {**service.info(), "loaded": service.is_ready()}


@app.post("/v1/rerank", response_model=RerankResponse, summary="文档重排序打分")
async def rerank(request: RerankRequest):
    """对 query 与候选文档逐一打分。

    请求示例：
        {"query": "怎么测交流电压？", "documents": ["...", "..."]}
    """
    if not request.query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    if not request.documents:
        raise HTTPException(status_code=400, detail="documents 不能为空")

    max_documents = settings.module.max_documents
    if len(request.documents) > max_documents:
        raise HTTPException(
            status_code=413,
            detail=f"单次最多处理 {max_documents} 条文档，当前 {len(request.documents)} 条，请分批调用",
        )

    if not service.is_ready():
        raise HTTPException(status_code=503, detail="重排序服务尚未就绪")

    try:
        scores = await service.compute_scores(request.query, request.documents)
    except Exception as exc:
        # 用占位符传参：日志消息里可能含大括号（如接口返回的 JSON），不能走 f-string
        logger.error("重排序打分失败：{}", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"重排序打分失败：{exc}") from exc

    logger.debug(f"完成 {len(request.documents)} 条文档的重排序打分")
    return {"scores": scores}


if __name__ == "__main__":
    uvicorn.run(app=app, host=settings.module.host, port=settings.module.port)
