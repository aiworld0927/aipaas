"""嵌入模型服务配置。

三组配置，全部来自环境变量：
  服务组：监听地址 / 端口 / 批次上限 / 并发上限 / 后端选择
  离线组：本地 BGE-M3 模型路径、设备、精度
  在线组：DashScope（OpenAI 兼容）embedding 接口参数

后端开关 EMBEDDING_BACKEND：
    offline（默认）  本地 BGE-M3，输出 dense + sparse
    online           远端 embedding 接口，仅输出 dense（sparse 预留）
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# 开发机默认模型目录；Linux 部署时通过 BGE_M3_PATH 覆盖
DEFAULT_MODEL_PATH = "D:/aigc/modelscope_cache/models/BAAI/bge-m3"
DEFAULT_ONLINE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


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
    device = os.getenv("BGE_DEVICE", "").strip()
    if device:
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


@dataclass
class OfflineEmbeddingConfig:
    """本地模型（离线）配置。"""

    model_path: str = field(default_factory=lambda: os.getenv("BGE_M3_PATH", DEFAULT_MODEL_PATH))
    device: str = field(default_factory=_resolve_device)
    use_fp16: bool = field(default_factory=lambda: _env_bool("BGE_FP16", False))
    # 固定开启原生 L2 归一化：Milvus 侧用 IP 检索，归一化后等价余弦相似度
    normalize_embeddings: bool = True


@dataclass
class OnlineEmbeddingConfig:
    """远端 embedding 接口（在线）配置。"""

    api_key: str = field(default_factory=lambda: _env_str("EMBEDDING_ONLINE_API_KEY", "OPENAI_API_KEY"))
    base_url: str = field(
        default_factory=lambda: _env_str("EMBEDDING_ONLINE_BASE_URL", "OPENAI_BASE_URL", DEFAULT_ONLINE_BASE_URL)
    )
    model: str = field(default_factory=lambda: _env_str("EMBEDDING_ONLINE_MODEL", "EMBEDDING_MODEL", "text-embedding-v4"))
    dimension: int = field(default_factory=lambda: _env_int("EMBEDDING_ONLINE_DIM", _env_int("EMBEDDING_DIM", 1536)))
    timeout: float = field(default_factory=lambda: float(os.getenv("EMBEDDING_ONLINE_TIMEOUT", "60")))


@dataclass
class EmbeddingWorkerConfig:
    # 后端：offline | online
    backend: str = field(default_factory=lambda: os.getenv("EMBEDDING_BACKEND", "offline").strip().lower())
    host: str = field(default_factory=lambda: os.getenv("MODEL_EMBEDDING_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("MODEL_EMBEDDING_PORT", 8101))
    max_batch_size: int = field(default_factory=lambda: _env_int("MODEL_EMBEDDING_MAX_BATCH", 64))
    # 推理并发上限：GPU 显存有限，默认串行
    max_concurrency: int = field(default_factory=lambda: _env_int("MODEL_EMBEDDING_MAX_CONCURRENCY", 1))
    offline: OfflineEmbeddingConfig = field(default_factory=OfflineEmbeddingConfig)
    online: OnlineEmbeddingConfig = field(default_factory=OnlineEmbeddingConfig)

    @property
    def is_online(self) -> bool:
        return self.backend == "online"


embedding_worker_config = EmbeddingWorkerConfig()
