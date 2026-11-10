"""重排序 HTTP 客户端。

对应独立部署的 apps/model-reranker-worker 服务。在线/离线后端的切换
在服务端通过 RERANKER_BACKEND 完成，业务侧只认一个地址。

函数名、入参、返回值与历史 rerank_documents 一致。

环境变量：
    RERANKER_SERVICE_URL       服务地址，默认 http://127.0.0.1:8102
    RERANKER_SERVICE_TIMEOUT   超时秒数，默认 60
"""
import os
from typing import Optional

import httpx

from aipaas_common.core.log import logger

_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    """惰性创建并复用 HTTP 连接池。"""
    global _client
    if _client is None:
        base_url = os.getenv("RERANKER_SERVICE_URL", "http://127.0.0.1:8102")
        timeout = float(os.getenv("RERANKER_SERVICE_TIMEOUT", "60"))
        _client = httpx.Client(base_url=base_url, timeout=timeout)
        logger.info(f"重排序服务客户端已初始化 | base_url={base_url} timeout={timeout}s")
    return _client


def rerank_documents(rewritten_query: str, documents: list[str]) -> list[float]:
    """对候选文档重排序打分。

    :param rewritten_query: 改写后的查询
    :param documents: 候选文档内容列表
    :return: 与 documents 顺序一一对应的分数列表
    """
    if not documents:
        return []

    try:
        response = _get_client().post("/v1/rerank", json={"query": rewritten_query, "documents": documents})
        response.raise_for_status()
        return [float(score) for score in response.json()["scores"]]
    except Exception as exc:
        logger.error("Rerank 调用失败：{}", exc, exc_info=True)
        raise
