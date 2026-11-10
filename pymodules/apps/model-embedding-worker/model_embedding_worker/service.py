"""后端门面：按 EMBEDDING_BACKEND 把请求分派到离线或在线实现。

离线：model_service（本地 BGE-M3，dense + sparse）
在线：online_service（远端接口，仅 dense）
"""
import importlib

from model_embedding_worker.config import embedding_worker_config


def _backend():
    # 延迟导入：在线模式无需加载 pymilvus / torch
    if embedding_worker_config.is_online:
        return importlib.import_module("model_embedding_worker.online_service")
    return importlib.import_module("model_embedding_worker.model_service")


async def ensure_ready() -> None:
    """服务启动时调用，完成模型加载或在线配置校验。"""
    await _backend().ensure_loaded()


def is_ready() -> bool:
    return _backend().is_loaded()


def info() -> dict:
    return _backend().info()


async def encode(texts: list[str]) -> dict:
    return await _backend().encode(texts)
