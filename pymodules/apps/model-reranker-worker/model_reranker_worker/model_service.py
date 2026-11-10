"""离线重排序后端：本地 bge-reranker-large 模型加载与打分。"""
import asyncio
from typing import Optional

from FlagEmbedding import FlagReranker

from aipaas_common.core.log import logger
from model_reranker_worker.config import reranker_worker_config

_reranker_model: Optional[FlagReranker] = None
_load_lock = asyncio.Lock()
_infer_semaphore = asyncio.Semaphore(reranker_worker_config.max_concurrency)


def _build_model() -> FlagReranker:
    """同步加载模型（在线程池中执行）。"""
    cfg = reranker_worker_config.offline
    logger.info(f"开始加载 Reranker 模型 | path={cfg.model_path} device={cfg.device} fp16={cfg.use_fp16}")
    model = FlagReranker(
        model_name_or_path=cfg.model_path,
        device=cfg.device,
        use_fp16=cfg.use_fp16,
    )
    logger.success("Reranker 模型加载完成")
    return model


async def ensure_loaded() -> None:
    """确保模型已加载；并发调用时只加载一次。"""
    global _reranker_model
    if _reranker_model is not None:
        return
    async with _load_lock:
        if _reranker_model is None:
            _reranker_model = await asyncio.to_thread(_build_model)


def is_loaded() -> bool:
    return _reranker_model is not None


def info() -> dict:
    cfg = reranker_worker_config.offline
    return {
        "backend": "offline",
        "model": cfg.model_path,
        "device": cfg.device,
        "fp16": cfg.use_fp16,
        "normalize": cfg.normalize,
    }


def _compute_sync(query: str, documents: list[str]) -> list[float]:
    """同步打分（在线程池中执行）。"""
    sentence_pairs = [[query, document] for document in documents]
    scores = _reranker_model.compute_score(sentence_pairs, normalize=reranker_worker_config.offline.normalize)

    # 单条输入时 compute_score 可能返回标量，统一转成列表
    if isinstance(scores, (int, float)):
        return [float(scores)]
    if hasattr(scores, "tolist"):
        scores = scores.tolist()
    return [float(score) for score in scores]


async def compute_scores(query: str, documents: list[str]) -> list[float]:
    """异步打分入口：推理在独立线程执行，并受并发上限保护。"""
    async with _infer_semaphore:
        return await asyncio.to_thread(_compute_sync, query, documents)
