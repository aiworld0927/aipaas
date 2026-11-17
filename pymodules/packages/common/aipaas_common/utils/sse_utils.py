"""SSE（Server-Sent Events）会话推送。

对外有两种用法：
- ``SSEHub``：可注入的会话队列管理器（推荐，FastAPI 依赖注入使用）；
- 模块级函数：委托给进程内默认 ``SSEHub``，兼容既有节点代码（``push_to_session`` 等）。

会话队列保存在进程内存中，因此同一 session 的“发起查询”和“订阅流”必须落在同一实例上。
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import Request

from aipaas_common.core.log import logger


class SSEEvent:
    READY = "ready"  # 连接建立
    PROGRESS = "progress"  # 任务节点进度
    DELTA = "delta"  # LLM 流式输出增量
    FINAL = "final"  # 最终完整答案
    ERROR = "error"  # 错误信息
    CLOSE = "__close__"  # 关闭连接信号


def _sse_pack(event: str, data: Dict[str, Any]) -> str:
    """打包一条 SSE 消息。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


class SSEHub:
    """Session 到消息队列的映射，线程安全（事件循环与工作线程都会访问）。"""

    def __init__(self) -> None:
        self._queues: Dict[str, "queue.Queue"] = {}
        self._lock = threading.Lock()

    def create(self, session_id: str) -> "queue.Queue":
        """创建并注册会话队列；同名会话重复创建时以新队列为准。"""
        stream_queue: "queue.Queue" = queue.Queue()
        with self._lock:
            self._queues[session_id] = stream_queue
        logger.debug("[SSE] 创建会话队列 session={} total={}", session_id, len(self._queues))
        return stream_queue

    def get(self, session_id: str) -> Optional["queue.Queue"]:
        with self._lock:
            return self._queues.get(session_id)

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._queues.pop(session_id, None)
        logger.debug("[SSE] 移除会话队列 session={}", session_id)

    def push(self, session_id: str, event: str, data: Dict[str, Any]) -> None:
        """推送事件；会话不存在时只记 warning（查询可能已结束或尚未订阅）。"""
        stream_queue = self.get(session_id)
        if stream_queue is None:
            logger.warning("[SSE] 会话队列不存在，丢弃事件 session={} event={}", session_id, event)
            return
        stream_queue.put({"event": event, "data": data})

    async def stream(self, session_id: str, request: Request) -> AsyncGenerator[str, None]:
        """SSE 生成器：先发 ready，再持续转发队列消息，客户端断开即退出。"""
        stream_queue = self.get(session_id)
        if stream_queue is None:
            logger.warning("[SSE] 订阅时会话队列不存在 session={}", session_id)
            return

        loop = asyncio.get_running_loop()
        try:
            yield _sse_pack(SSEEvent.READY, {})

            while True:
                if await request.is_disconnected():
                    logger.info("[SSE] 客户端断开连接 session={}", session_id)
                    break

                try:
                    # 队列是阻塞 API，放到线程池等待，避免阻塞事件循环
                    msg = await loop.run_in_executor(None, stream_queue.get, True, 1.0)
                except queue.Empty:
                    continue

                event = msg.get("event")
                if event == SSEEvent.CLOSE:
                    logger.info("[SSE] 收到关闭信号 session={}", session_id)
                    break
                yield _sse_pack(event, msg.get("data") or {})
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            logger.info("[SSE] 客户端连接中断 session={}", session_id)
        except Exception as exc:  # 生成器内的异常必须吞掉，否则会污染 StreamingResponse
            logger.error("[SSE] 推送异常 session={} error={}", session_id, exc, exc_info=True)
        finally:
            self.remove(session_id)


# 进程内默认 Hub：供节点等无法注入的代码直接使用
default_sse_hub = SSEHub()


def get_sse_hub() -> SSEHub:
    """FastAPI 依赖：获取进程内 SSE 会话管理器。"""
    return default_sse_hub


def get_sse_queue(session_id: str) -> Optional["queue.Queue"]:
    return default_sse_hub.get(session_id)


def create_sse_queue(session_id: str) -> "queue.Queue":
    return default_sse_hub.create(session_id)


def remove_sse_queue(session_id: str) -> None:
    default_sse_hub.remove(session_id)


def push_to_session(session_id: str, event: str, data: Dict[str, Any]) -> None:
    default_sse_hub.push(session_id, event, data)


async def sse_generator(session_id: str, request: Request) -> AsyncGenerator[str, None]:
    async for chunk in default_sse_hub.stream(session_id, request):
        yield chunk
