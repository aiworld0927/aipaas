"""嵌入服务应用入口：配置引导、生命周期、路由装配与进程启动。

``load_settings`` 必须先于业务模块导入执行（唯一权威来源，失败即终止启动）。
启动命令：``python -m model_embedding_worker.api.app``
"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from aipaas_common.core.lifespan import AppLifespan
from aipaas_common.core.log import logger
from model_embedding_worker import service as embedding_service
from model_embedding_worker.api.routers import health, inference


from aipaas_common.conf.settings import load_settings

# 配置引导：必须先于业务模块导入
settings = load_settings("model_embedding_worker")

def build_lifespan() -> AppLifespan:
    """启动时完成模型加载或在线配置校验。

    失败不中断进程，由 /ready 返回 503 暴露原因，方便容器探针与运维定位。
    """

    async def prepare() -> None:
        try:
            await embedding_service.ensure_ready()
        except Exception as exc:
            logger.error("嵌入服务启动准备失败，/ready 将返回 503：{}", exc, exc_info=True)

    return AppLifespan().add_hooks(startup=prepare)


def create_app() -> FastAPI:
    """创建嵌入服务的 FastAPI 实例。"""
    app = FastAPI(
        title="BGE-M3 嵌入模型服务",
        description="AIPaaS 嵌入模型独立推理服务，支持离线（本地模型）与在线（远端接口）两种后端",
        lifespan=build_lifespan(),
    )
    app.include_router(health.router)
    app.include_router(inference.router)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app=app, host=settings.module.host, port=settings.module.port)
