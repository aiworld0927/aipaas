"""向量生成接口。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aipaas_common.conf.settings import get_settings
from aipaas_common.core.log import logger
from model_embedding_worker import service as backend

router = APIRouter(tags=["向量化"])


class EmbeddingRequest(BaseModel):
    """向量化请求体。"""

    embedding_documents: list[str] = Field(..., description="需要生成向量的文本列表")


class EmbeddingResponse(BaseModel):
    """向量化响应体。"""

    dense: list[list[float]] = Field(..., description="稠密向量，与输入文本一一对应")
    sparse: list[dict[int, float]] = Field(..., description="稀疏向量；在线后端固定为空列表")


@router.post("/v1/embeddings", response_model=EmbeddingResponse, summary="生成向量")
async def embeddings(request: EmbeddingRequest):
    """批量生成向量。

    请求示例：
        {"embedding_documents": ["如何测量交流电压？"]}
    """
    texts = request.embedding_documents
    if not texts:
        raise HTTPException(status_code=400, detail="embedding_documents 不能为空")

    max_batch = get_settings().module.max_batch_size
    if len(texts) > max_batch:
        raise HTTPException(
            status_code=413,
            detail=f"单次最多处理 {max_batch} 条文本，当前 {len(texts)} 条，请分批调用",
        )

    if not backend.is_ready():
        raise HTTPException(status_code=503, detail="嵌入服务尚未就绪")

    try:
        result = await backend.encode(texts)
    except Exception as exc:
        # 用占位符传参：日志消息里可能含大括号（如接口返回的 JSON），不能走 f-string
        logger.error("向量生成失败：{}", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"向量生成失败：{exc}") from exc

    logger.debug(f"完成 {len(texts)} 条文本的向量生成")
    return result
