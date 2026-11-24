"""模块命名空间注册表：module_name（= Python 包名）→ 配置组装模型。"""
from aipaas_common.conf.schemas.common import CommonSettings
from aipaas_common.conf.schemas.app_ingest_worker import IngestSettings
from aipaas_common.conf.schemas.model_embedding_worker import ModelEmbeddingSettings
from aipaas_common.conf.schemas.model_reranker_worker import ModelRerankerSettings
from aipaas_common.conf.schemas.app_query_worker import QuerySettings

MODULE_SCHEMAS = {
    "app_ingest_worker": IngestSettings,
    "app_query_worker": QuerySettings,
    "model_embedding_worker": ModelEmbeddingSettings,
    "model_reranker_worker": ModelRerankerSettings,
}

__all__ = [
    "CommonSettings",
    "IngestSettings",
    "QuerySettings",
    "ModelEmbeddingSettings",
    "ModelRerankerSettings",
    "MODULE_SCHEMAS",
]
