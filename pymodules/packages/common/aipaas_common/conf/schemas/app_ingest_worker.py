"""app_ingest_worker 模块配置 schema。"""
from pydantic import BaseModel

from aipaas_common.conf.schemas.common import CommonSettings, HostPortSettings


class IngestWorkerModule(HostPortSettings):
    """文档导入流程的切片 / 商品名识别 / 图片处理参数。"""

    max_content_length: int
    min_content_length: int
    item_name_chunk_k: int
    item_name_chunk_size: int
    image_extensions: set[str]


class IngestSettings(BaseModel):
    """load_settings("app_ingest_worker") 的返回类型：common + module 两段。"""

    model_config = {"extra": "forbid"}
    common: CommonSettings
    module: IngestWorkerModule
