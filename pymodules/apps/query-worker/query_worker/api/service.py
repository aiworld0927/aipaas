"""查询流程的接口定义（全异步版）。"""
import asyncio
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse, StreamingResponse

from aipaas_common.conf.settings import load_settings

# 配置引导：必须在导入业务模块前完成加载（唯一权威来源，失败即终止启动）
settings = load_settings("query_worker")

from aipaas_common.core.context import request_id_ctx_var
from aipaas_storage.mongo_history_utils import clear_history, get_recent_messages
from query_worker.graph.main_v2 import KBQueryWorkflowV2
from query_worker.graph.state import create_default_query_state
from aipaas_common.utils.sse_utils import (
    create_sse_queue, SSEEvent, push_to_session, sse_generator,
)
from aipaas_common.utils.task_utils import (
    update_task_status, TASK_STATUS_PROCESSING, get_task_result,
    TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, clear_task, get_done_task_list,
)
from aipaas_common.core.lifespan import AppLifespan
from aipaas_common.core.log import logger

# 生命周期编排：按注册顺序启动、逆序关闭；后续 MySQL / Elasticsearch 等组件继续 add
app_lifespan = AppLifespan()
if settings.common.nacos is not None and settings.common.nacos.enabled:
    # 开关打开时才加载 Nacos 对接包，未启用环境不引入 Nacos SDK
    from aipaas_nacos import nacos_lifespan

    app_lifespan.add(nacos_lifespan("svc-query"))

app = FastAPI(
    title="金融标准知识库-查询API",
    description="此文档是金融标准化知识库查询流程的API接口说明",
    lifespan=app_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/chat.html")
async def chat():
    current_dir_parent_path = Path(__file__).absolute().parents[3]
    html_path = current_dir_parent_path / "pages" / "chat.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"没有查询到页面，地址为：{html_path}")
    return FileResponse(html_path)


class QueryRequest(BaseModel):
    query: str = Field(..., description="查询内容")
    session_id: str = Field(None, description="会话ID")
    is_stream: bool = Field(False, description="是否流式返回")


@app.post("/query")
async def query(request: QueryRequest):
    """查询接口（全异步版）。

    - 流式分支：用 asyncio.create_task 后台执行 run_query_graph_async，立即返回 session_id，
      客户端随后订阅 /stream/{session_id}。
    - 非流式分支：直接 await run_query_graph_async，同步拿到答案。
    """
    request_id_ctx_var.set(uuid.uuid4())

    user_query = request.query.strip()
    session_id = request.session_id if request.session_id else str(uuid.uuid4())
    is_stream = request.is_stream

    if is_stream:
        create_sse_queue(session_id)
        # 后台协程：非阻塞，让当前请求立即返回
        asyncio.create_task(run_query_graph_async(session_id, user_query, is_stream))
        logger.info("流式分支已创建后台协程, session_id=%s", session_id)
        return {"message": "结果正在处理中...", "session_id": session_id}
    else:
        await run_query_graph_async(session_id, user_query, is_stream)
        answer = get_task_result(session_id, "answer", "")
        return {
            "message": "处理完成！",
            "session_id": session_id,
            "answer": answer,
            "done_list": get_done_task_list(session_id),
        }


async def run_query_graph_async(session_id: str, user_query: str, is_stream: bool = True):
    """LangGraph 工作流的异步执行入口。

    节点内部的 SSE DELTA/FINAL 事件已经通过 push_to_session 推到进程内 queue；
    这里只负责调用 graph.arun（走 ainvoke/astream）、处理异常、补推 FINAL 兜底。
    """
    logger.info(f"开始流程图处理...is_stream:{is_stream}::session_id:{session_id}:user_query:{user_query}")
    clear_task(session_id)
    update_task_status(session_id, TASK_STATUS_PROCESSING, is_stream)

    try:
        workflow = KBQueryWorkflowV2()
        state = create_default_query_state(
            original_query=user_query, session_id=session_id, is_stream=is_stream,
        )
        # graph.arun(stream=True) 会内部 astream，每个 chunk 是节点结束后的 state diff；
        # 我们只需要它把整条链路跑完，最终 state 在最后一次 chunk 里
        await workflow.arun(state, stream=is_stream)
        update_task_status(session_id, TASK_STATUS_COMPLETED, is_stream)

        image_urls = state.get("image_urls", [])
        push_to_session(
            session_id=session_id,
            event=SSEEvent.FINAL,
            data={
                "answer": get_task_result(session_id, "answer"),
                "state": "completed",
                "image_urls": image_urls,
            },
        )

    except Exception as e:
        logger.exception(f"流程执行异常: {e}")
        update_task_status(session_id, TASK_STATUS_FAILED, is_stream)
        if is_stream:
            push_to_session(session_id, SSEEvent.ERROR, {"error": str(e)})


@app.get("/stream/{session_id}")
async def stream(session_id: str, request: Request):
    """SSE 订阅端：前端订阅后，run_query_graph_async 通过 push_to_session 推事件。"""
    logger.info("调用流式/stream..., session_id=%s", session_id)
    return StreamingResponse(
        sse_generator(session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/history/{session_id}")
async def clear_chat_history(session_id: str):
    """清空指定会话的历史记录。"""
    count = await asyncio.to_thread(clear_history, session_id)
    return {"message": "历史会话已清空", "deleted_count": count}


@app.get("/history/{session_id}")
async def history(session_id: str, limit: int = 50):
    """查询当前会话历史记录。"""
    try:
        records = await asyncio.to_thread(get_recent_messages, session_id, limit=limit)
        return {"session_id": session_id, "items": records}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history error: {e}")


@app.get("/health")
async def health():
    """服务健康检查。"""
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host=settings.module.host, port=settings.module.port)