"""model_reranker_worker 模块配置 schema。"""
from typing import Literal, Optional

from pydantic import BaseModel, SecretStr

from aipaas_common.conf.schemas.common import CommonSettings, HostPortSettings, _Base


class OfflineRerankerSettings(_Base):
    """本地 bge-reranker-large：device 为 None 时由服务代码侧按 torch 探测自动选择。"""

    model_path: str
    device: Optional[str] = None
    use_fp16: bool
    normalize: bool = True


class OnlineRerankerSettings(_Base):
    api_key: Optional[SecretStr] = None
    model: str
    instruct: str

    @property
    def api_key_str(self) -> str:
        return self.api_key.get_secret_value() if self.api_key else ""


class ModelRerankerWorkerModule(HostPortSettings):
    backend: Literal["offline", "online"]
    max_documents: int
    max_concurrency: int
    offline: OfflineRerankerSettings
    online: OnlineRerankerSettings

    @property
    def is_online(self) -> bool:
        return self.backend == "online"


class ModelRerankerSettings(BaseModel):
    """load_settings("model_reranker_worker") 的返回类型。"""

    model_config = {"extra": "forbid"}
    common: CommonSettings
    module: ModelRerankerWorkerModule
