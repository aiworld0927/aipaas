"""在线重排序后端：调用 DashScope TextReRank 接口。

dashscope SDK 为同步实现，统一用 asyncio.to_thread 包装成异步调用。
"""
import asyncio

import dashscope

from aipaas_common.core.log import logger
from model_reranker_worker.config import reranker_worker_config

_infer_semaphore = asyncio.Semaphore(reranker_worker_config.max_concurrency)


async def ensure_loaded() -> None:
    """在线后端无本地模型，仅校验必要配置。"""
    cfg = reranker_worker_config.online
    if not cfg.api_key:
        raise RuntimeError("在线 rerank 后端缺少 API Key，请配置 TEXT_RERANK_API_KEY 或 OPENAI_API_KEY")
    logger.info(f"在线 rerank 后端就绪 | model={cfg.model}")


def is_loaded() -> bool:
    return bool(reranker_worker_config.online.api_key)


def info() -> dict:
    cfg = reranker_worker_config.online
    return {
        "backend": "online",
        "model": cfg.model,
        "instruct": cfg.instruct,
    }


def _call_sync(query: str, documents: list[str]) -> list[float]:
    """同步调用 DashScope（在线程池中执行）。"""
    cfg = reranker_worker_config.online
    response = dashscope.TextReRank.call(
        api_key=cfg.api_key,
        model=cfg.model,
        query=query,
        documents=documents,
        top_n=len(documents),
        return_documents=False,
        instruct=cfg.instruct,
    )

    status_code = response.get("status_code")
    if status_code != 200:
        raise RuntimeError(f"DashScope rerank 调用失败: {response.get('message')}")

    results = response.output.get("results", [])
    scores = [0.0] * len(documents)
    for item in results:
        scores[int(item.get("index"))] = float(item.get("relevance_score"))
    return scores


async def compute_scores(query: str, documents: list[str]) -> list[float]:
    """异步打分入口（受并发上限保护）。"""
    async with _infer_semaphore:
        return await asyncio.to_thread(_call_sync, query, documents)
