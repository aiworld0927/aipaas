"""重排序模型服务配置。

三组配置，全部来自环境变量：
  服务组：监听地址 / 端口 / 文档数上限 / 并发上限 / 后端选择
  离线组：本地 bge-reranker-large 模型路径、设备、精度
  在线组：DashScope TextReRank 接口参数

后端开关 RERANKER_BACKEND：
    offline（默认）  本地 bge-reranker-large
    online           DashScope 在线 rerank（兼容旧值 dashscope）
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# 开发机默认模型目录；Linux 部署时通过 BGE_RERANKER_LARGE 覆盖
DEFAULT_MODEL_PATH = "D:/aigc/modelscope_cache/models/BAAI/bge-reranker-large"
DEFAULT_ONLINE_INSTRUCT = "针对给定的查询，检索能够解答该查询的相关段落"


def _env_bool(name: str, default: bool = False) -> bool:
    """把 .env 中的 1/0、True/False 等写法统一解析为布尔值。"""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _env_str(primary: str, fallback: str, default: str = "") -> str:
    """优先读专用变量，未配置时回退到通用变量。"""
    return os.getenv(primary) or os.getenv(fallback) or default


def _resolve_device() -> str:
    """未显式配置设备时自动选择：有可用 GPU 用 cuda，否则 cpu。"""
    device = os.getenv("BGE_RERANKER_DEVICE", "").strip()
    if device:
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _resolve_backend() -> str:
    """统一后端取值：兼容历史配置中的 local / dashscope 写法。"""
    value = os.getenv("RERANKER_BACKEND", "offline").strip().lower()
    return "online" if value in ("online", "dashscope") else "offline"


@dataclass
class OfflineRerankerConfig:
    """本地模型（离线）配置。"""

    model_path: str = field(default_factory=lambda: os.getenv("BGE_RERANKER_LARGE", DEFAULT_MODEL_PATH))
    device: str = field(default_factory=_resolve_device)
    use_fp16: bool = field(default_factory=lambda: _env_bool("BGE_RERANKER_FP16", False))
    # 固定开启归一化打分：业务侧断崖截断阈值（0.5 / 0.25）按归一化分数设定
    normalize: bool = True


@dataclass
class OnlineRerankerConfig:
    """DashScope 在线 rerank 配置。"""

    api_key: str = field(default_factory=lambda: _env_str("TEXT_RERANK_API_KEY", "OPENAI_API_KEY"))
    model: str = field(default_factory=lambda: os.getenv("TEXT_RERANK_MODEL", "qwen3-rerank"))
    instruct: str = field(default_factory=lambda: os.getenv("TEXT_RERANK_INSTRUCT", DEFAULT_ONLINE_INSTRUCT))


@dataclass
class RerankerWorkerConfig:
    # 后端：offline | online
    backend: str = field(default_factory=_resolve_backend)
    host: str = field(default_factory=lambda: os.getenv("MODEL_RERANKER_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("MODEL_RERANKER_PORT", 8102))
    max_documents: int = field(default_factory=lambda: _env_int("MODEL_RERANKER_MAX_DOCS", 128))
    # 推理并发上限：GPU 显存有限，默认串行
    max_concurrency: int = field(default_factory=lambda: _env_int("MODEL_RERANKER_MAX_CONCURRENCY", 1))
    offline: OfflineRerankerConfig = field(default_factory=OfflineRerankerConfig)
    online: OnlineRerankerConfig = field(default_factory=OnlineRerankerConfig)

    @property
    def is_online(self) -> bool:
        return self.backend == "online"


reranker_worker_config = RerankerWorkerConfig()
