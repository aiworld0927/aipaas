"""后端门面：按配置 backend 把请求分派到离线或在线实现。

离线：model_service（本地 bge-reranker-large）
在线：online_service（DashScope TextReRank）
"""
import importlib

from aipaas_common.conf.settings import get_settings


def _backend():
    # 延迟导入：在线模式无需加载 FlagEmbedding / torch
    if get_settings().module.is_online:
        return importlib.import_module("model_reranker_worker.online_service")
    return importlib.import_module("model_reranker_worker.model_service")


async def ensure_ready() -> None:
    """服务启动时调用，完成模型加载或在线配置校验。"""
    await _backend().ensure_loaded()


def is_ready() -> bool:
    return _backend().is_loaded()


def info() -> dict:
    return _backend().info()


async def compute_scores(query: str, documents: list[str]) -> list[float]:
    return await _backend().compute_scores(query, documents)
