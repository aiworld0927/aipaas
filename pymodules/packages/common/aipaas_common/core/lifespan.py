"""FastAPI 应用生命周期编排。

服务启动/退出时需要初始化的组件会持续增加（Nacos 注册、MySQL 连接池、Elasticsearch 客户端、
消息队列等），本模块把多个组件的 lifespan 组合成一个 FastAPI lifespan，避免在各 service.py
中层层嵌套 async with。

使用方式::

    lifespan = AppLifespan()
    lifespan.add(nacos_lifespan("svc-ingest"))       # 组件自带完整 lifespan
    lifespan.add(mysql_lifespan(settings))           # 未来：MySQL 初始化/释放
    lifespan.add_hooks(init_es, close_es)            # 或只提供 init/dispose 钩子

    app = FastAPI(lifespan=lifespan)

启动按注册顺序执行，退出按逆序执行（与 AsyncExitStack 语义一致）；任一组件启动失败会
自动回滚已启动的组件并向上抛出，由进程启动流程决定是否终止。
"""
from __future__ import annotations

import inspect
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

# FastAPI 风格的 lifespan：入参 app，返回异步上下文管理器
LifespanFactory = Callable[[Any], AbstractAsyncContextManager[None]]
# 简单初始化/释放钩子，同步或异步均可
LifecycleHook = Callable[[], Awaitable[None] | None]


class AppLifespan:
    """组合多个组件生命周期的 FastAPI lifespan。"""

    def __init__(self) -> None:
        self._components: list[LifespanFactory] = []

    def add(self, lifespan: LifespanFactory) -> "AppLifespan":
        """注册一个完整的组件 lifespan（如 aipaas_nacos.nacos_lifespan）。"""
        self._components.append(lifespan)
        return self

    def add_hooks(
        self,
        startup: LifecycleHook | None = None,
        shutdown: LifecycleHook | None = None,
    ) -> "AppLifespan":
        """注册简单的初始化/释放钩子，适合 MySQL、ES 等只需 init/dispose 的组件。"""
        self._components.append(_hooks_lifespan(startup, shutdown))
        return self

    @asynccontextmanager
    async def __call__(self, app: Any) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for component in self._components:
                await stack.enter_async_context(component(app))
            yield


def _hooks_lifespan(
    startup: LifecycleHook | None,
    shutdown: LifecycleHook | None,
) -> LifespanFactory:
    """把一对 init/dispose 钩子包装成 FastAPI lifespan。"""

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        if startup is not None:
            await _invoke(startup)
        try:
            yield
        finally:
            if shutdown is not None:
                await _invoke(shutdown)

    return lifespan


async def _invoke(hook: LifecycleHook) -> None:
    """执行钩子；同步函数直接调用，协程函数等待完成。"""
    result = hook()
    if inspect.isawaitable(result):
        await result
