"""model_embedding_worker 模块配置 schema。"""
from typing import Literal, Optional

from pydantic import BaseModel, SecretStr

from aipaas_common.conf.schemas.common import CommonSettings, HostPortSettings, _Base


class OfflineEmbeddingSettings(_Base):
    """本地 BGE-M3：device 为 None 时由服务代码侧按 torch 探测自动选择。"""

    model_path: str
    device: Optional[str] = None
    use_fp16: bool
    normalize_embeddings: bool = True


class OnlineEmbeddingSettings(_Base):
    api_key: Optional[SecretStr] = None
    base_url: str
    model: str
    dimension: int
    timeout: float

    @property
    def api_key_str(self) -> str:
        return self.api_key.get_secret_value() if self.api_key else ""


class ModelEmbeddingWorkerModule(HostPortSettings):
    backend: Literal["offline", "online"]
    max_batch_size: int
    max_concurrency: int
    offline: OfflineEmbeddingSettings
    online: OnlineEmbeddingSettings

    @property
    def is_online(self) -> bool:
        return self.backend == "online"


class ModelEmbeddingSettings(BaseModel):
    """load_settings("model_embedding_worker") 的返回类型。"""

    model_config = {"extra": "forbid"}
    common: CommonSettings
    module: ModelEmbeddingWorkerModule
