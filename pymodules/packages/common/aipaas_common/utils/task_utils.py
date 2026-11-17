"""内存态任务追踪（单进程）。

提供 ``TaskTracker`` 类（可注入）与模块级默认实例函数（兼容既有节点代码）。
任务状态、已完成/运行中节点、结果字段都保存在进程内存，所以同一任务的所有操作
必须落在同一实例上；多副本部署时需换成外部存储实现。
"""
from __future__ import annotations

import threading
from typing import Dict, List

from aipaas_common.utils.sse_utils import default_sse_hub

TASK_STATUS_PENDING = "pending"
TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"

# 节点名 -> 中文名映射（用于前端展示）
# key 必须与 LangGraph 的 add_node("xxx", ...) 中的节点名一致
_NODE_NAME_TO_CN: Dict[str, str] = {
    "upload_file": "开始上传文件",
    "node_entry": "检查文件",
    "node_pdf_to_md": "PDF转Markdown",
    "node_md_img": "Markdown图片处理",
    "node_item_name_recognition": "主体名称识别",
    "node_document_split": "文档切分",
    "node_bge_embedding": "向量生成",
    "node_import_kg": "导入知识图谱",
    "node_import_milvus": "导入向量库",
    "__end__": "处理完成",
    "END": "处理完成",
    # --- Query 流程节点 ---
    "node_item_name_confirm": "确认问题产品",
    "node_answer_output": "生成答案",
    "node_rerank": "重排序",
    "node_rrf": "倒排融合",
    "node_web_search_mcp": "网络搜索",
    "node_search_embedding": "切片搜索",
    "node_search_embedding_hyde": "切片搜索(假设性文档)",
    "node_multi_search": "多路搜索",
    "node_query_kg": "查询知识图谱",
    "node_join": "多路搜索合并",
}


def _to_cn(node_name: str) -> str:
    return _NODE_NAME_TO_CN.get(node_name, node_name)


class TaskTracker:
    """进程内任务进度追踪器（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._running_list: Dict[str, List[str]] = {}
        self._done_list: Dict[str, List[str]] = {}
        self._status: Dict[str, str] = {}
        self._result: Dict[str, Dict[str, str]] = {}

    def _ensure(self, task_id: str) -> None:
        """确保 task_id 对应的数据结构已初始化（调用方需持锁）。"""
        self._running_list.setdefault(task_id, [])
        self._done_list.setdefault(task_id, [])
        self._result.setdefault(task_id, {})

    def add_running(self, task_id: str, node_name: str, is_stream: bool = False) -> None:
        with self._lock:
            self._ensure(task_id)
            running = self._running_list[task_id]
            if node_name not in running:
                running.append(node_name)
        if is_stream:
            self.push_progress(task_id)

    def add_done(self, task_id: str, node_name: str, is_stream: bool = False) -> None:
        with self._lock:
            self._ensure(task_id)
            # 完成后从运行中移除同名节点
            self._running_list[task_id] = [n for n in self._running_list[task_id] if n != node_name]
            done = self._done_list[task_id]
            if node_name not in done:
                done.append(node_name)
        if is_stream:
            self.push_progress(task_id)

    def set_result(self, task_id: str, key: str, value: str) -> None:
        with self._lock:
            self._ensure(task_id)
            self._result[task_id][key] = value

    def get_result(self, task_id: str, key: str, default: str = "") -> str:
        with self._lock:
            self._ensure(task_id)
            return self._result.get(task_id, {}).get(key, default)

    def get_status(self, task_id: str) -> str:
        with self._lock:
            return self._status.get(task_id, "")

    def get_done_list(self, task_id: str) -> List[str]:
        with self._lock:
            self._ensure(task_id)
            return [_to_cn(n) for n in self._done_list.get(task_id, [])]

    def get_running_list(self, task_id: str) -> List[str]:
        with self._lock:
            self._ensure(task_id)
            return [_to_cn(n) for n in self._running_list.get(task_id, [])]

    def update_status(self, task_id: str, status_name: str, push_queue: bool = False) -> None:
        with self._lock:
            self._status[task_id] = status_name
        if push_queue:
            self.push_progress(task_id)

    def push_progress(self, task_id: str) -> None:
        default_sse_hub.push(
            task_id,
            "progress",
            {
                "status": self.get_status(task_id),
                "done_list": self.get_done_list(task_id),
                "running_list": self.get_running_list(task_id),
            },
        )

    def clear(self, task_id: str) -> None:
        with self._lock:
            self._running_list.pop(task_id, None)
            self._done_list.pop(task_id, None)
            self._status.pop(task_id, None)
            self._result.pop(task_id, None)


# 进程内默认实例：节点等无法注入的代码通过下方模块级函数使用
default_task_tracker = TaskTracker()


def get_task_tracker() -> TaskTracker:
    """FastAPI 依赖：获取进程内任务追踪器。"""
    return default_task_tracker


def add_running_task(task_id: str, node_name: str, is_stream: bool = False) -> None:
    default_task_tracker.add_running(task_id, node_name, is_stream)


def add_done_task(task_id: str, node_name: str, is_stream: bool = False) -> None:
    default_task_tracker.add_done(task_id, node_name, is_stream)


def set_task_result(task_id: str, key: str, value: str) -> None:
    default_task_tracker.set_result(task_id, key, value)


def get_task_result(task_id: str, key: str, default: str = "") -> str:
    return default_task_tracker.get_result(task_id, key, default)


def get_task_status(task_id: str) -> str:
    return default_task_tracker.get_status(task_id)


def get_done_task_list(task_id: str) -> List[str]:
    return default_task_tracker.get_done_list(task_id)


def get_running_task_list(task_id: str) -> List[str]:
    return default_task_tracker.get_running_list(task_id)


def update_task_status(task_id: str, status_name: str, push_queue: bool = False) -> None:
    default_task_tracker.update_status(task_id, status_name, push_queue)


def task_push_queue(task_id: str) -> None:
    default_task_tracker.push_progress(task_id)


def clear_task(task_id: str) -> None:
    default_task_tracker.clear(task_id)
