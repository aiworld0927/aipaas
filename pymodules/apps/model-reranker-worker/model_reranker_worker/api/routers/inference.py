"""文档重排序接口。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aipaas_common.conf.settings import get_settings
from aipaas_common.core.log import logger
from model_reranker_worker import service as backend

router = APIRouter(tags=["重排序"])


class RerankRequest(BaseModel):
    """重排序请求体。"""

    query: str = Field(..., description="查询文本")
    documents: list[str] = Field(..., description="候选文档列表")


class RerankResponse(BaseModel):
    """重排序响应体，scores 与 documents 顺序一一对应。"""

    scores: list[float] = Field(..., description="每个候选文档的相关性分数")


@router.post("/v1/rerank", response_model=RerankResponse, summary="文档重排序打分")
async def rerank(request: RerankRequest):
    """对 query 与候选文档逐一打分。

    请求示例：
        {"query": "怎么测交流电压？", "documents": ["...", "..."]}
    """
    if not request.query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    if not request.documents:
        raise HTTPException(status_code=400, detail="documents 不能为空")

    max_documents = get_settings().module.max_documents
    if len(request.documents) > max_documents:
        raise HTTPException(
            status_code=413,
            detail=f"单次最多处理 {max_documents} 条文档，当前 {len(request.documents)} 条，请分批调用",
        )

    if not backend.is_ready():
        raise HTTPException(status_code=503, detail="重排序服务尚未就绪")

    try:
        scores = await backend.compute_scores(request.query, request.documents)
    except Exception as exc:
        # 用占位符传参：日志消息里可能含大括号（如接口返回的 JSON），不能走 f-string
        logger.error("重排序打分失败：{}", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"重排序打分失败：{exc}") from exc

    logger.debug(f"完成 {len(request.documents)} 条文档的重排序打分")
    return {"scores": scores}
