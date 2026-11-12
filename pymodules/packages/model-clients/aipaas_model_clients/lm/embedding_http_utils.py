"""BGE-M3 嵌入模型 HTTP 客户端。

对应独立部署的 apps/model-embedding-worker 服务，函数名、入参、返回值
与历史进程内实现保持一致，业务侧调用逻辑无需改动。
服务端支持离线（本地 BGE-M3）与在线（远端接口）两种后端，由服务侧配置决定。

配置来源：统一配置 common.embedding_service 节点（url / timeout）。
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
        cfg = get_settings().common.embedding_service
        _client = httpx.Client(base_url=cfg.url, timeout=cfg.timeout)
        logger.info(f"嵌入模型服务客户端已初始化 | base_url={cfg.url} timeout={cfg.timeout}s")
    return _client


def _get_async_client() -> httpx.AsyncClient:
    """惰性创建并复用异步 HTTP 连接池。"""
    global _async_client
    if _async_client is None:
        cfg = get_settings().common.embedding_service
        _async_client = httpx.AsyncClient(base_url=cfg.url, timeout=cfg.timeout)
        logger.info(f"嵌入模型服务异步客户端已初始化 | base_url={cfg.url} timeout={cfg.timeout}s")
    return _async_client


def _parse_sparse(payload: dict) -> list[dict]:
    """把 JSON 序列化后字符串 key 的稀疏向量还原为 int key（Milvus 稀疏向量字段要求）。"""
    return [{int(token_id): float(weight) for token_id, weight in item.items()} for item in payload["sparse"]]


def generate_embeddings(texts: list[str]) -> dict:
    """为文本列表生成稠密 + 稀疏混合向量（远端模型服务，同步）。

    :param texts: 要生成嵌入的文本列表，单文本也需封装为列表
    :return: {"dense": 嵌套列表, "sparse": 字典列表}
    """
    if not isinstance(texts, list) or len(texts) == 0:
        logger.warning("生成向量入参不合法，texts必须为非空列表")
        raise ValueError("参数texts必须是包含文本的非空列表")

    logger.info(f"开始为{len(texts)}条文本生成混合向量嵌入（远端服务）")
    try:
        response = _get_client().post("/v1/embeddings", json={"embedding_documents": texts})
        response.raise_for_status()
        payload = response.json()
        logger.success(f"{len(texts)}条文本向量生成完成")
        return {"dense": payload["dense"], "sparse": _parse_sparse(payload)}
    except Exception as exc:
        logger.error("文本向量生成失败：{}", exc, exc_info=True)
        raise


async def agenerate_embeddings(texts: list[str]) -> dict:
    """generate_embeddings 的异步版本，避免阻塞 FastAPI / LangGraph 事件循环。"""
    if not isinstance(texts, list) or len(texts) == 0:
        logger.warning("生成向量入参不合法，texts必须为非空列表")
        raise ValueError("参数texts必须是包含文本的非空列表")

    logger.info(f"开始为{len(texts)}条文本生成混合向量嵌入（远端服务 async）")
    try:
        response = await _get_async_client().post("/v1/embeddings", json={"embedding_documents": texts})
        response.raise_for_status()
        payload = response.json()
        logger.success(f"{len(texts)}条文本向量生成完成（async）")
        return {"dense": payload["dense"], "sparse": _parse_sparse(payload)}
    except Exception as exc:
        logger.error("文本向量生成失败（async）：{}", exc, exc_info=True)
        raise
