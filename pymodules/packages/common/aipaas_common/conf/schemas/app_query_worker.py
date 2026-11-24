"""app_query_worker 模块配置 schema。"""
from pydantic import BaseModel

from aipaas_common.conf.schemas.common import CommonSettings, HostPortSettings


class QueryWorkerModule(HostPortSettings):
    """查询流程的商品名确认阈值 / 动态 TopK / 断崖截断 / 上下文预算。"""

    query_high_threshold: float
    query_mid_threshold: float
    rerank_max_topk: int
    rerank_min_topk: int
    rerank_gap_abs: float
    rerank_gap_ratio: float
    max_context_chars: int


class QuerySettings(BaseModel):
    """load_settings("app_query_worker") 的返回类型。"""

    model_config = {"extra": "forbid"}
    common: CommonSettings
    module: QueryWorkerModule
