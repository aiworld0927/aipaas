"""后台协程管理。

``asyncio.create_task`` 返回的任务如果没有强引用，可能在事件循环中被垃圾回收，
导致长流程（文档导入、查询图执行）中途消失。本模块统一持有任务引用，并在进程
退出时给正在运行的任务一个收尾窗口，超时后取消，避免“悄悄丢任务”。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable

from aipaas_common.core.log import logger


class BackgroundTaskManager:
    """持有后台任务强引用，并提供优雅关闭。"""

    def __init__(self, name: str = "background") -> None:
        self._name = name
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Awaitable[Any], *, name: str | None = None) -> asyncio.Task[Any]:
        """启动后台协程并持有引用；任务结束后自动释放。"""
        task = asyncio.create_task(coro, name=name or self._name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("[{}] 后台任务异常退出：{}", self._name, exc, exc_info=exc)

    @property
    def pending(self) -> int:
        """当前仍在运行的任务数。"""
        return len(self._tasks)

    async def aclose(self, timeout: float = 10.0) -> None:
        """优雅关闭：先等待任务自行结束，超时后取消剩余任务。"""
        if not self._tasks:
            return
        tasks = list(self._tasks)
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info(
            "[{}] 后台任务收尾完成 | 已完成={} 被取消={}",
            self._name,
            len(done),
            len(pending),
        )
