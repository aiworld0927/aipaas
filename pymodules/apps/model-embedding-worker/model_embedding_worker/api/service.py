"""嵌入模型服务的 HTTP 接口定义（全异步）。

启动方式：
    uvicorn model_embedding_worker.api.service:app --host 0.0.0.0 --port 8101
"""
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from aipaas_common.conf.settings import load_settings

# 配置引导：必须在导入业务模块前完成加载（唯一权威来源，失败即终止启动）
settings = load_settings("model_embedding_worker")

from aipaas_common.core.log import logger
from model_embedding_worker import service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动时完成就绪准备（加载本地模型或校验在线配置）。

    失败不中断进程：/ready 返回 503 暴露原因，便于容器探针与运维定位。
    """
    try:
        await service.ensure_ready()
    except Exception as exc:
        logger.error("嵌入服务启动准备失败，/ready 将返回 503：{}", exc, exc_info=True)
    yield


app = FastAPI(
    title="BGE-M3 嵌入模型服务",
    description="AIPaaS 嵌入模型独立推理服务，支持离线（本地模型）与在线（远端接口）两种后端",
    lifespan=lifespan,
)


class EmbeddingRequest(BaseModel):
    """向量化请求体。"""

    embedding_documents: list[str] = Field(..., description="需要生成向量的文本列表")


class EmbeddingResponse(BaseModel):
    """向量化响应体。"""

    dense: list[list[float]] = Field(..., description="稠密向量，与输入文本一一对应")
    sparse: list[dict[int, float]] = Field(..., description="稀疏向量；在线后端固定为空列表")


@app.get("/health", summary="存活探针")
async def health():
    """仅表示进程存活，不检查模型状态（供容器 liveness 探针使用）。"""
    return {"status": "ok", "service": "model-embedding-worker"}


@app.get("/ready", summary="就绪探针")
async def ready():
    """后端就绪返回 200，否则 503（供容器 readiness 探针使用）。"""
    if not service.is_ready():
        raise HTTPException(status_code=503, detail="嵌入服务尚未就绪")
    return {"status": "ready", **service.info()}


@app.get("/v1/model/info", summary="模型信息")
async def model_info():
    """返回当前后端、模型、设备、维度等信息。"""
    return {**service.info(), "loaded": service.is_ready()}


@app.post("/v1/embeddings", response_model=EmbeddingResponse, summary="生成向量")
async def embeddings(request: EmbeddingRequest):
    """批量生成向量。

    请求示例：
        {"embedding_documents": ["如何测量交流电压？"]}
    """
    texts = request.embedding_documents
    if not texts:
        raise HTTPException(status_code=400, detail="embedding_documents 不能为空")

    max_batch = settings.module.max_batch_size
    if len(texts) > max_batch:
        raise HTTPException(
            status_code=413,
            detail=f"单次最多处理 {max_batch} 条文本，当前 {len(texts)} 条，请分批调用",
        )

    if not service.is_ready():
        raise HTTPException(status_code=503, detail="嵌入服务尚未就绪")

    try:
        result = await service.encode(texts)
    except Exception as exc:
        # 用占位符传参：日志消息里可能含大括号（如接口返回的 JSON），不能走 f-string
        logger.error("向量生成失败：{}", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"向量生成失败：{exc}") from exc

    logger.debug(f"完成 {len(texts)} 条文本的向量生成")
    return result


if __name__ == "__main__":
    uvicorn.run(app=app, host=settings.module.host, port=settings.module.port)
