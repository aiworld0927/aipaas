"""离线嵌入后端：本地 BGE-M3 模型加载与混合向量编码。

输出结构与历史实现保持一致：
    {"dense": [[...], ...], "sparse": [{token_id: weight}, ...]}
"""
import asyncio
from typing import Optional

from pymilvus.model.hybrid import BGEM3EmbeddingFunction

from aipaas_common.core.log import logger
from model_embedding_worker.config import embedding_worker_config

_model: Optional[BGEM3EmbeddingFunction] = None
_load_lock = asyncio.Lock()
# 推理并发闸门：GPU 显存有限，默认串行（Python 3.10+ 允许在无事件循环时创建）
_infer_semaphore = asyncio.Semaphore(embedding_worker_config.max_concurrency)


def _build_model() -> BGEM3EmbeddingFunction:
    """同步加载模型（在线程池中执行）。"""
    cfg = embedding_worker_config.offline
    logger.info(
        f"开始加载 BGE-M3 模型 | path={cfg.model_path} device={cfg.device} "
        f"fp16={cfg.use_fp16} normalize={cfg.normalize_embeddings}"
    )
    model = BGEM3EmbeddingFunction(
        model_name=cfg.model_path,
        device=cfg.device,
        use_fp16=cfg.use_fp16,
        normalize_embeddings=cfg.normalize_embeddings,
    )
    logger.success("BGE-M3 模型加载完成")
    return model


async def ensure_loaded() -> None:
    """确保模型已加载；并发调用时只加载一次。"""
    global _model
    if _model is not None:
        return
    async with _load_lock:
        if _model is None:
            _model = await asyncio.to_thread(_build_model)


def is_loaded() -> bool:
    return _model is not None


def info() -> dict:
    """返回离线模型信息。"""
    cfg = embedding_worker_config.offline
    return {
        "backend": "offline",
        "model": cfg.model_path,
        "device": cfg.device,
        "fp16": cfg.use_fp16,
        "normalize_embeddings": cfg.normalize_embeddings,
        "dim": getattr(_model, "dim", None),
    }


def _encode_sync(texts: list[str]) -> dict:
    """同步编码（在线程池中执行），CSR 稀疏矩阵按行切分为字典。"""
    model = _model
    embeddings = model.encode_documents(texts)
    csr_array = embeddings["sparse"]

    processed_sparse = []
    for index in range(len(texts)):
        start = csr_array.indptr[index]
        end = csr_array.indptr[index + 1]
        token_ids = csr_array.indices[start:end].tolist()
        weights = csr_array.data[start:end].tolist()
        processed_sparse.append(dict(zip(token_ids, weights)))

    return {
        "dense": [dense_vector.tolist() for dense_vector in embeddings["dense"]],
        "sparse": processed_sparse,
    }


async def encode(texts: list[str]) -> dict:
    """异步编码入口：推理在独立线程执行，并受并发上限保护。"""
    async with _infer_semaphore:
        return await asyncio.to_thread(_encode_sync, texts)
