from functools import wraps
import time
from typing import Mapping

from aipaas_common.core.log import logger
from aipaas_common.utils.task_utils import add_running_task, add_done_task


def _trace_id(state) -> str:
    if isinstance(state, Mapping):
        return str(state.get("session_id") or state.get("task_id") or "-")
    return "-"

# 通用日志：封装到base类里面了
def node_log(node_name: str):
    def deco(func):
        @wraps(func)
        def wrapper(state, *args, **kwargs):
            trace_id = _trace_id(state)
            start_ts = time.time()
            logger.info(f"[{node_name}] 节点开始，追踪ID={trace_id}")
            try:
                # 1. 任务状态记录处理
                add_running_task(trace_id, node_name,state.get('is_stream',False))
                result = func(state, *args, **kwargs)
                add_done_task(trace_id, node_name,state.get('is_stream',False))
                cost_ms = int((time.time() - start_ts) * 1000)
                logger.info(f"[{node_name}] 节点完成，追踪ID={trace_id}，耗时={cost_ms}ms")
                return result
            except Exception:
                logger.exception(f"[{node_name}] 节点异常，追踪ID={trace_id}")
                raise
        return wrapper
    return deco

def step_log(step_name: str):
    """
    步骤日志装饰器：
    - 自动打印 步骤开始 / 步骤完成 / 步骤异常（含堆栈）
    - 不吞异常，保持原有业务语义
    """
    def deco(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_ts = time.time()
            logger.info(f"[{step_name}] 步骤开始")
            try:
                result = func(*args, **kwargs)
                cost_ms = int((time.time() - start_ts) * 1000)
                logger.info(f"[{step_name}] 步骤完成，耗时={cost_ms}ms")
                return result
            except Exception:
                logger.exception(f"[{step_name}] 步骤异常")
                raise
        return wrapper
    return deco
