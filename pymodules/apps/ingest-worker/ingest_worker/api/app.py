"""导入服务应用入口：配置引导、生命周期、路由装配与进程启动。

``load_settings`` 必须先于业务模块导入执行（唯一权威来源，失败即终止启动）。
启动命令：``python -m ingest_worker.api.app``
"""
from __future__ import annotations

import uvicorn

from aipaas_common.conf.settings import load_settings

# 配置引导：必须先于业务模块导入
settings = load_settings("ingest_worker")

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from aipaas_common.core.lifespan import AppLifespan
from aipaas_common.core.observability import install_observability
from ingest_worker.api.dependencies import background_tasks, get_ingest_settings
from ingest_worker.api.routers import embedding, metadata, pages, parse, summary, tasks

SERVICE_NAME = "svc-ingest"


def build_lifespan() -> AppLifespan:
    """生命周期：Nacos 注册（条件加载）+ 后台任务收尾。"""
    lifespan = AppLifespan()
    settings = get_ingest_settings()
    if settings.common.nacos is not None and settings.common.nacos.enabled:
        # 开关打开时才加载 Nacos 对接包，未启用环境不引入 Nacos SDK
        from aipaas_nacos import nacos_lifespan
        lifespan.add(nacos_lifespan(SERVICE_NAME))
    lifespan.add_hooks(shutdown=background_tasks.aclose)
    return lifespan


def create_app() -> FastAPI:
    """创建导入服务的 FastAPI 实例。"""
    app = FastAPI(
        title="金融标准化-导入API",
        description="此文档是金融标准化知识库导入流程的API接口说明",
        lifespan=build_lifespan(),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_observability(app, SERVICE_NAME)
    app.include_router(tasks.router)
    app.include_router(parse.router)
    app.include_router(summary.router)
    app.include_router(metadata.router)
    app.include_router(embedding.router)
    app.include_router(pages.router)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app=app, host=settings.module.host, port=settings.module.port)
