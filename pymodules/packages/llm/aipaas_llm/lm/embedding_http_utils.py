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


def _get_client() -> httpx.Client:
    """惰性创建并复用 HTTP 连接池。"""
    global _client
    if _client is None:
        cfg = get_settings().common.embedding_service
        _client = httpx.Client(base_url=cfg.url, timeout=cfg.timeout)
        logger.info(f"嵌入模型服务客户端已初始化 | base_url={cfg.url} timeout={cfg.timeout}s")
    return _client


def generate_embeddings(texts: list[str]) -> dict:
    """为文本列表生成稠密 + 稀疏混合向量（远端模型服务）。

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

        # JSON 序列化后稀疏向量的 key 会变成字符串，这里还原为 int
        # （Milvus 稀疏向量字段要求 key 为整数维度号）
        sparse = [{int(token_id): float(weight) for token_id, weight in item.items()} for item in payload["sparse"]]

        logger.success(f"{len(texts)}条文本向量生成完成")
        return {"dense": payload["dense"], "sparse": sparse}
    except Exception as exc:
        logger.error("文本向量生成失败：{}", exc, exc_info=True)
        raise
