"""SSE 流式订阅接口。"""
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from aipaas_common.core.log import logger
from app_query_worker.api.dependencies import SSEHubDep

router = APIRouter(tags=["流式查询"])


@router.get("/stream/{session_id}")
async def stream(session_id: str, request: Request, hub: SSEHubDep):
    """订阅会话事件流；事件由查询图执行过程中推送。"""
    logger.info("调用流式/stream..., session_id={}", session_id)
    return StreamingResponse(
        hub.stream(session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
