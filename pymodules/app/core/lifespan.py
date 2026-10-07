from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.fileserver_client_manager import fileserver_client_manager
from app.clients.kubernetes_client_manager import kubernetes_client_manager
# from app.clients.embedding_client_manager import embedding_client_manager
# from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import iasp_mysql_client_manager
# from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.logger import logger

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    logger.info("Starting pyserv application...")

    # 初始化数据库连接池
    iasp_mysql_client_manager.init()

    # 初始化 Kubernetes 配置
    kubernetes_client_manager.init()

    # 发送配置到文件服务器
    fileserver_client_manager.init()
    # embedding_client_manager.init()
    # qdrant_client_manager.init()
    # es_client_manager.init()
    # dw_mysql_client_manager.init()
    logger.info("Application startup complete")
    yield
    # 关闭时清理
    logger.info("Shutting down pyserv application...")

    # FastAPI 应用结束前执行

    # await qdrant_client_manager.close()
    # await es_client_manager.close()
    await iasp_mysql_client_manager.close()
    # await dw_mysql_client_manager.close()
    # 关闭时清理
    logger.info("Application close complete")
