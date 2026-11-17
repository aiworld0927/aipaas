"""查询服务的依赖提供者（FastAPI Depends）。

集中声明可注入组件，便于替换实现与单元测试：
- 配置：``QuerySettings``
- 任务追踪：``TaskTracker``
- SSE 会话管理：``SSEHub``
- 后台任务：``BackgroundTaskManager``
- LangGraph 工作流：进程内单例，避免每次请求重复编译
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from aipaas_common.conf.schemas import QuerySettings
from aipaas_common.conf.settings import get_settings
from aipaas_common.core.background import BackgroundTaskManager
from aipaas_common.utils.sse_utils import SSEHub, get_sse_hub
from aipaas_common.utils.task_utils import TaskTracker, get_task_tracker
from query_worker.api.query_service import QueryService
from query_worker.graph.main_v2 import KBQueryWorkflowV2

# 进程级后台任务管理器：持有任务强引用，进程退出时统一收尾
background_tasks = BackgroundTaskManager("query-background")


def get_query_settings() -> QuerySettings:
    """获取已加载的查询服务配置。"""
    return get_settings()  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_query_workflow() -> KBQueryWorkflowV2:
    """LangGraph 编译开销大，进程内单例复用。"""
    return KBQueryWorkflowV2()


@lru_cache(maxsize=1)
def get_query_service() -> QueryService:
    """查询执行服务单例。"""
    return QueryService(
        tracker=get_task_tracker(),
        sse_hub=get_sse_hub(),
        workflow=get_query_workflow(),
        background=background_tasks,
    )


SettingsDep = Annotated[QuerySettings, Depends(get_query_settings)]
TaskTrackerDep = Annotated[TaskTracker, Depends(get_task_tracker)]
SSEHubDep = Annotated[SSEHub, Depends(get_sse_hub)]
QueryServiceDep = Annotated[QueryService, Depends(get_query_service)]
