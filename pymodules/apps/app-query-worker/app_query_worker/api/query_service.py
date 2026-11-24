"""查询业务服务：会话初始化、图执行、SSE 事件推送。"""
from __future__ import annotations

from aipaas_common.core.background import BackgroundTaskManager
from aipaas_common.core.log import logger
from aipaas_common.utils.sse_utils import SSEEvent, SSEHub
from aipaas_common.utils.task_utils import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PROCESSING,
    TaskTracker,
)
from app_query_worker.graph.main_v2 import KBQueryWorkflowV2
from app_query_worker.graph.state import create_default_query_state


class QueryService:
    """把路由层的协议处理与 LangGraph 执行解耦。"""

    def __init__(
        self,
        tracker: TaskTracker,
        sse_hub: SSEHub,
        workflow: KBQueryWorkflowV2,
        background: BackgroundTaskManager,
    ) -> None:
        self._tracker = tracker
        self._sse = sse_hub
        self._workflow = workflow
        self._background = background

    def submit_stream(self, session_id: str, user_query: str) -> None:
        """流式分支：创建会话队列并后台执行，立即返回。"""
        self._sse.create(session_id)
        self._background.spawn(
            self.run(session_id, user_query, is_stream=True),
            name=f"query:{session_id}",
        )
        logger.info("流式分支已创建后台协程, session_id={}", session_id)

    async def run(self, session_id: str, user_query: str, is_stream: bool = True) -> None:
        """执行查询图，更新任务状态并推送 FINAL/ERROR 事件。"""
        logger.info(
            f"开始流程图处理...is_stream:{is_stream}::session_id:{session_id}:user_query:{user_query}"
        )
        self._tracker.clear(session_id)
        self._tracker.update_status(session_id, TASK_STATUS_PROCESSING, is_stream)

        try:
            state = create_default_query_state(
                original_query=user_query,
                session_id=session_id,
                is_stream=is_stream,
            )
            # 节点内部通过 push_to_session 推送 DELTA/FINAL；这里只负责把整条链路跑完
            await self._workflow.arun(state, stream=is_stream)
            self._tracker.update_status(session_id, TASK_STATUS_COMPLETED, is_stream)

            self._sse.push(
                session_id,
                SSEEvent.FINAL,
                {
                    "answer": self._tracker.get_result(session_id, "answer"),
                    "state": "completed",
                    "image_urls": state.get("image_urls", []),
                },
            )
        except Exception as exc:
            logger.exception(f"流程执行异常: {exc}")
            self._tracker.update_status(session_id, TASK_STATUS_FAILED, is_stream)
            if is_stream:
                self._sse.push(session_id, SSEEvent.ERROR, {"error": str(exc)})

    def answer(self, session_id: str) -> str:
        return self._tracker.get_result(session_id, "answer")

    def done_list(self, session_id: str) -> list[str]:
        return self._tracker.get_done_list(session_id)
