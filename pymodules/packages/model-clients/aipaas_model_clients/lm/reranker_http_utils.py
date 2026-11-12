"""重排序 HTTP 客户端。

对应独立部署的 apps/model-reranker-worker 服务。在线/离线后端的切换
在服务端通过配置 backend 完成，业务侧只认一个地址。

函数名、入参、返回值与历史 rerank_documents 一致。

配置来源：统一配置 common.rerank_service 节点（url / timeout）。
"""
from typing import Optional

import httpx

from aipaas_common.conf.settings import get_settings
from aipaas_common.core.log import logger

_client: Optional[httpx.Client] = None
_async_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.Client:
    """惰性创建并复用 HTTP 连接池。"""
    global _client
    if _client is None:
        cfg = get_settings().common.rerank_service
        _client = httpx.Client(base_url=cfg.url, timeout=cfg.timeout)
        logger.info(f"重排序服务客户端已初始化 | base_url={cfg.url} timeout={cfg.timeout}s")
    return _client


def _get_async_client() -> httpx.AsyncClient:
    """惰性创建并复用异步 HTTP 连接池。"""
    global _async_client
    if _async_client is None:
        cfg = get_settings().common.rerank_service
        _async_client = httpx.AsyncClient(base_url=cfg.url, timeout=cfg.timeout)
        logger.info(f"重排序服务异步客户端已初始化 | base_url={cfg.url} timeout={cfg.timeout}s")
    return _async_client


def rerank_documents(rewritten_query: str, documents: list[str]) -> list[float]:
    """对候选文档重排序打分（同步）。"""
    if not documents:
        return []

    try:
        response = _get_client().post("/v1/rerank", json={"query": rewritten_query, "documents": documents})
        response.raise_for_status()
        return [float(score) for score in response.json()["scores"]]
    except Exception as exc:
        logger.error("Rerank 调用失败：{}", exc, exc_info=True)
        raise


async def arerank_documents(rewritten_query: str, documents: list[str]) -> list[float]:
    """rerank_documents 的异步版本，避免阻塞事件循环。"""
    if not documents:
        return []

    try:
        response = await _get_async_client().post(
            "/v1/rerank", json={"query": rewritten_query, "documents": documents}
        )
        response.raise_for_status()
        return [float(score) for score in response.json()["scores"]]
    except Exception as exc:
        logger.error("Rerank 调用失败（async）：{}", exc, exc_info=True)
        raise
