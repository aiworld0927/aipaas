"""导入任务接口：健康检查、文件上传与进度查询。"""
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from aipaas_common.core.log import logger
from ingest_worker.api.dependencies import ImportServiceDep, TaskTrackerDep

router = APIRouter(tags=["导入任务"])


class UploadResponse(BaseModel):
    """多文件上传结果：一个文件对应一个 TaskID。"""

    code: int = Field(200, description="业务状态码")
    message: str = Field(..., description="处理结果说明")
    task_ids: list[str] = Field(default_factory=list, description="本次上传生成的任务 ID 列表")


class TaskStatusResponse(BaseModel):
    """单个任务的处理进度。"""

    code: int = Field(200, description="业务状态码")
    task_id: str = Field(..., description="任务 ID")
    status: str = Field("", description="任务全局状态：pending/processing/completed/failed")
    done_list: list[str] = Field(default_factory=list, description="已完成节点（中文展示名）")
    running_list: list[str] = Field(default_factory=list, description="运行中节点（中文展示名）")


@router.get("/health", summary="健康检查")
async def health():
    """检查服务是否正常，返回 200 表示服务可用。"""
    return {"status": "ok", "service": "svc-ingest"}


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="文件上传接口",
    description="支持多文件批量上传，自动触发知识库导入全流程",
)
async def upload_files(service: ImportServiceDep, files: list[UploadFile] = File(...)):
    """接收多文件并触发导入流程，返回每个文件对应的 TaskID。

    流程：按「日期/任务ID」本地落盘 → MinIO 持久化 → 启动 LangGraph 后台任务。
    """
    task_ids = await service.upload(files)
    return UploadResponse(
        code=200,
        message=f" 文件上传成功, total: {len(files)}",
        task_ids=task_ids,
    )


@router.get(
    "/status/{task_id}",
    response_model=TaskStatusResponse,
    summary="任务状态查询",
    description="根据TaskID查询单个文件的处理进度和全局状态",
)
async def get_task_progress(task_id: str, tracker: TaskTrackerDep):
    """查询单个任务的状态与节点进度（数据全部来自进程内内存）。"""
    info = TaskStatusResponse(
        code=200,
        task_id=task_id,
        status=tracker.get_status(task_id),
        done_list=tracker.get_done_list(task_id),
        running_list=tracker.get_running_list(task_id),
    )
    logger.info(
        f"[{task_id}] 任务状态查询，当前状态：{info.status}，已完成节点：{info.done_list}"
    )
    return info
