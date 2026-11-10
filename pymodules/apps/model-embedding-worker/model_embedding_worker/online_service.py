"""在线嵌入后端：调用 OpenAI 兼容的远端 embedding 接口（默认为 DashScope 兼容模式）。

说明：
    远端 embedding 只返回稠密向量，因此 sparse 固定返回空列表。
    稀疏向量（BGE-M3 lexical weights）的在线化实现留作后续扩展点：
    届时在 encode() 内补齐 sparse 生成即可，接口结构无需变更。
"""
import asyncio
from typing import Optional

from openai import AsyncOpenAI

from aipaas_common.core.log import logger
from model_embedding_worker.config import embedding_worker_config

_client: Optional[AsyncOpenAI] = None
_infer_semaphore = asyncio.Semaphore(embedding_worker_config.max_concurrency)


def _get_client() -> AsyncOpenAI:
    """惰性创建异步客户端，复用连接池。"""
    global _client
    if _client is None:
        cfg = embedding_worker_config.online
        _client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout)
        logger.info(f"在线 embedding 客户端已初始化 | base_url={cfg.base_url} model={cfg.model}")
    return _client


async def ensure_loaded() -> None:
    """在线后端无本地模型，仅校验必要配置。"""
    cfg = embedding_worker_config.online
    if not cfg.api_key:
        raise RuntimeError("在线 embedding 后端缺少 API Key，请配置 EMBEDDING_ONLINE_API_KEY 或 OPENAI_API_KEY")
    logger.info(f"在线 embedding 后端就绪 | model={cfg.model} dim={cfg.dimension}")


def is_loaded() -> bool:
    return bool(embedding_worker_config.online.api_key)


def info() -> dict:
    cfg = embedding_worker_config.online
    return {
        "backend": "online",
        "model": cfg.model,
        "base_url": cfg.base_url,
        "dim": cfg.dimension,
        "sparse_supported": False,
    }


async def encode(texts: list[str]) -> dict:
    """调用远端接口生成稠密向量，sparse 预留为空。"""
    cfg = embedding_worker_config.online
    async with _infer_semaphore:
        response = await _get_client().embeddings.create(model=cfg.model, input=texts)

    # 接口返回顺序不保证与入参一致，按 index 还原
    ordered = sorted(response.data, key=lambda item: item.index)
    return {
        "dense": [item.embedding for item in ordered],
        "sparse": [],
    }
