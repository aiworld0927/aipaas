"""导入业务服务：文件落盘、MinIO 持久化、后台执行 LangGraph 全流程。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence

from fastapi import UploadFile

from aipaas_common.conf.schemas import IngestSettings
from aipaas_common.core.background import BackgroundTaskManager
from aipaas_common.core.exceptions import BusinessError
from aipaas_common.core.log import logger
from aipaas_common.utils.path_util import PROJECT_ROOT
from aipaas_common.utils.task_utils import TaskTracker
from aipaas_storage.minio_utils import aupload_file_to_minio
from ingest_worker.graph.main import KBImportWorkflow

_CHUNK_SIZE = 1024 * 1024  # 1MB


class ImportService:
    """把 HTTP 上传与导入流程图解耦，路由层只负责协议转换。"""

    def __init__(
        self,
        settings: IngestSettings,
        tracker: TaskTracker,
        workflow: KBImportWorkflow,
        background: BackgroundTaskManager,
    ) -> None:
        self._settings = settings
        self._tracker = tracker
        self._workflow = workflow
        self._background = background

    async def upload(self, files: Sequence[UploadFile]) -> list[str]:
        """保存上传文件并触发后台导入，返回每个文件对应的 TaskID。"""
        date_time_dir = datetime.now().strftime("%Y%m%d")
        data_dir = PROJECT_ROOT / "output_files" / date_time_dir
        task_ids: list[str] = []

        for file in files:
            task_id = str(uuid.uuid4())
            task_ids.append(task_id)
            logger.info(
                f"[{task_id}] 开始处理上传文件，文件名：{file.filename}，文件类型：{file.content_type}"
            )

            # 每个任务独立目录：output/YYYYMMDD/TaskID，避免多文件重名冲突
            local_dir = data_dir / task_id
            local_dir.mkdir(parents=True, exist_ok=True)
            import_file_path = str(local_dir / str(file.filename))

            # 标记「文件上传」阶段为运行中，前端轮询可查
            self._tracker.add_running(task_id, "upload_file")

            with open(import_file_path, "wb") as out_file:
                while chunk := await file.read(_CHUNK_SIZE):
                    out_file.write(chunk)
            logger.info(f"[{task_id}] 文件已保存至本地，路径：{import_file_path}")

            # MinIO 失败不阻断本地处理流程
            minio_object_name = f"pdf_files/{date_time_dir}/{file.filename}"
            try:
                await aupload_file_to_minio(
                    file_path=import_file_path,
                    object_name=minio_object_name,
                    content_type=str(file.content_type),
                )
                logger.info(f"[{task_id}] 文件已成功上传至MinIO，对象名：{minio_object_name}")
            except Exception as exc:
                logger.warning(
                    f"[{task_id}] 文件上传MinIO失败，将继续执行本地处理流程，异常信息：{str(exc)}",
                    exc_info=True,
                )

            self._tracker.add_done(task_id, "upload_file")

            # 后台执行整条导入链路，不阻塞上传响应
            self._background.spawn(
                self._run_graph(task_id, str(local_dir), import_file_path),
                name=f"import:{task_id}",
            )
            logger.info(f"[{task_id}] 已将LangGraph全流程加入后台任务，任务已启动")

        logger.info(f"多文件上传处理完毕，共处理{len(files)}个文件，生成TaskID列表：{task_ids}")
        return task_ids

    async def _run_graph(self, task_id: str, file_dir: str, import_file_path: str) -> None:
        """执行 LangGraph 全流程，并同步任务状态与节点进度。"""
        try:
            self._tracker.update_status(task_id, "processing")

            init_state = {
                "task_id": task_id,
                "file_dir": file_dir,
                "import_file_path": import_file_path,
            }

            events = await self._workflow.arun(init_state, stream=True)
            for event in events:
                for node_name in event:
                    self._tracker.add_done(task_id, node_name)

            self._tracker.update_status(task_id, "completed")
        except BusinessError as exc:
            # 可预期的业务失败（如 MinerU 判定文件无法解析）：只给修复提示，不打印堆栈
            self._tracker.update_status(task_id, "failed")
            logger.error(f"[{task_id}] {exc}")
        except Exception as exc:
            self._tracker.update_status(task_id, "failed")
            logger.info(f"[{task_id}] LangGraph全流程执行失败，异常信息：{str(exc)}", exc_info=True)
