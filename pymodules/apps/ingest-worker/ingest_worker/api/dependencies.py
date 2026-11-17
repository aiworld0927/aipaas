"""导入服务的依赖提供者（FastAPI Depends）。

集中声明本应用的可注入组件，路由层只依赖这些类型别名，便于替换实现与测试：
- 配置：``IngestSettings``
- 任务追踪：``TaskTracker``
- 后台任务：``BackgroundTaskManager``
- LangGraph 工作流：进程内复用同一个已编译图，避免每次请求重复编译
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from aipaas_common.conf.schemas import IngestSettings
from aipaas_common.conf.settings import get_settings
from aipaas_common.core.background import BackgroundTaskManager
from aipaas_common.utils.task_utils import TaskTracker, get_task_tracker
from ingest_worker.api.import_service import ImportService
from ingest_worker.graph.main import KBImportWorkflow

# 进程级后台任务管理器：持有任务强引用，进程退出时统一收尾
background_tasks = BackgroundTaskManager("ingest-background")


def get_ingest_settings() -> IngestSettings:
    """获取已加载的导入服务配置。"""
    return get_settings()  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_import_workflow() -> KBImportWorkflow:
    """LangGraph 编译开销大，进程内单例复用。"""
    return KBImportWorkflow()


@lru_cache(maxsize=1)
def get_import_service() -> ImportService:
    """上传/状态查询共用的应用服务单例。"""
    return ImportService(
        settings=get_ingest_settings(),
        tracker=get_task_tracker(),
        workflow=get_import_workflow(),
        background=background_tasks,
    )


SettingsDep = Annotated[IngestSettings, Depends(get_ingest_settings)]
TaskTrackerDep = Annotated[TaskTracker, Depends(get_task_tracker)]
ImportServiceDep = Annotated[ImportService, Depends(get_import_service)]
