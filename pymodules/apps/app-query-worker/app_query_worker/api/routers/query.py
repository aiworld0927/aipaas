"""查询与健康检查接口。"""
import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from aipaas_common.core.log import logger
from app_query_worker.api.dependencies import QueryServiceDep

router = APIRouter(tags=["查询"])


class QueryRequest(BaseModel):
    """查询请求体。"""

    query: str = Field(..., description="查询内容")
    session_id: str | None = Field(None, description="会话ID")
    is_stream: bool = Field(False, description="是否流式返回")


class QueryResponse(BaseModel):
    """查询响应体。

    - 流式：只返回 ``message`` + ``session_id``，客户端随后订阅 /stream/{session_id}
    - 非流式：额外返回 ``answer`` 与 ``done_list``
    """

    message: str
    session_id: str
    answer: str | None = None
    done_list: list[str] | None = None


@router.get("/health")
async def health():
    """服务健康检查。"""
    return {"ok": True}


@router.post("/query", response_model=QueryResponse, response_model_exclude_none=True)
async def query(request: QueryRequest, service: QueryServiceDep):
    """知识库查询（全异步）。

    - 流式：后台执行查询图并立即返回 session_id，客户端随后订阅 /stream/{session_id}
    - 非流式：等待执行完成，直接返回答案与已完成节点
    """
    user_query = request.query.strip()
    session_id = request.session_id or str(uuid.uuid4())

    if request.is_stream:
        service.submit_stream(session_id, user_query)
        return QueryResponse(message="结果正在处理中...", session_id=session_id)

    await service.run(session_id, user_query, is_stream=False)
    logger.info("非流式查询完成, session_id={}", session_id)
    return QueryResponse(
        message="处理完成！",
        session_id=session_id,
        answer=service.answer(session_id),
        done_list=service.done_list(session_id),
    )
