from functools import wraps
import inspect
import time
from typing import Mapping

from aipaas_common.core.log import logger
from aipaas_common.utils.task_utils import add_running_task, add_done_task


def _trace_id(state) -> str:
    if isinstance(state, Mapping):
        return str(state.get("session_id") or state.get("task_id") or "-")
    return "-"


def node_log(node_name: str):
    """节点日志装饰器，兼容同步和异步节点。

    - async def 节点：返回 async wrapper，内部 await func(...)；
    - sync def 节点：返回 sync wrapper，内部直接调 func(...)。
    两种都保留原始函数签名（functools.wraps + 透传 coroutine 标记）。
    """

    def deco(func):
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(state, *args, **kwargs):
                trace_id = _trace_id(state)
                is_stream = state.get("is_stream", False)
                logger.info(f"[{node_name}] 节点开始（async），追踪ID={trace_id}")
                start_ts = time.time()
                try:
                    add_running_task(trace_id, node_name, is_stream=is_stream)
                    result = await func(state, *args, **kwargs)
                    add_done_task(trace_id, node_name, is_stream=is_stream)
                    cost_ms = int((time.time() - start_ts) * 1000)
                    logger.info(f"[{node_name}] 节点完成，追踪ID={trace_id}，耗时={cost_ms}ms")
                    return result
                except Exception:
                    logger.exception(f"[{node_name}] 节点异常，追踪ID={trace_id}")
                    raise

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(state, *args, **kwargs):
                trace_id = _trace_id(state)
                is_stream = state.get("is_stream", False)
                logger.info(f"[{node_name}] 节点开始，追踪ID={trace_id}")
                start_ts = time.time()
                try:
                    add_running_task(trace_id, node_name, is_stream=is_stream)
                    result = func(state, *args, **kwargs)
                    add_done_task(trace_id, node_name, is_stream=is_stream)
                    cost_ms = int((time.time() - start_ts) * 1000)
                    logger.info(f"[{node_name}] 节点完成，追踪ID={trace_id}，耗时={cost_ms}ms")
                    return result
                except Exception:
                    logger.exception(f"[{node_name}] 节点异常，追踪ID={trace_id}")
                    raise

            return sync_wrapper

    return deco


def step_log(step_name: str):
    """步骤日志装饰器，同样兼容 sync / async。"""

    def deco(func):
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_ts = time.time()
                logger.info(f"[{step_name}] 步骤开始")
                try:
                    result = await func(*args, **kwargs)
                    cost_ms = int((time.time() - start_ts) * 1000)
                    logger.info(f"[{step_name}] 步骤完成，耗时={cost_ms}ms")
                    return result
                except Exception:
                    logger.exception(f"[{step_name}] 步骤异常")
                    raise

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
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

            return sync_wrapper

    return deco
