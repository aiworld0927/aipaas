"""会话历史接口。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aipaas_common.core.log import logger
from aipaas_storage.mongo_history_utils import clear_history, get_recent_messages

router = APIRouter(tags=["会话历史"])


class HistoryResponse(BaseModel):
    """会话历史记录。"""

    session_id: str
    items: list[dict] = Field(default_factory=list)


class HistoryClearResponse(BaseModel):
    """清空会话历史的结果。"""

    message: str
    deleted_count: int


@router.delete("/history/{session_id}", response_model=HistoryClearResponse)
async def clear_chat_history(session_id: str):
    """清空指定会话的历史记录。"""
    count = await asyncio.to_thread(clear_history, session_id)
    return HistoryClearResponse(message="历史会话已清空", deleted_count=count)


@router.get("/history/{session_id}", response_model=HistoryResponse)
async def history(session_id: str, limit: int = 50):
    """查询当前会话历史记录。"""
    try:
        records = await asyncio.to_thread(get_recent_messages, session_id, limit=limit)
    except Exception as exc:
        logger.error("查询会话历史失败 session_id={} error={}", session_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"history error: {exc}") from exc
    return HistoryResponse(session_id=session_id, items=records)
