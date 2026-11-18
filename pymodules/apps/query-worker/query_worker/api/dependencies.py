"""查询服务的依赖提供者（FastAPI Depends）。

集中声明可注入组件，便于替换实现与单元测试：
- 配置：``QuerySettings``
- 任务追踪：``TaskTracker``
- SSE 会话管理：``SSEHub``
- 后台任务：``BackgroundTaskManager``
- LangGraph 工作流：进程内单例，避免每次请求重复编译
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from aipaas_common.conf.schemas import QuerySettings
from aipaas_common.conf.settings import get_settings
from aipaas_common.core.background import BackgroundTaskManager
from aipaas_common.utils.sse_utils import SSEHub, get_sse_hub
from aipaas_common.utils.task_utils import TaskTracker, get_task_tracker
from aipaas_generation import (
    AuditSink as GenerationAuditSink,
    GenerationConfig,
    GenerationService,
    InMemoryAuditSink as InMemoryGenerationAuditSink,
    JsonlAuditSink as JsonlGenerationAuditSink,
    LLMClientManager,
    RefusalPolicy,
    RefusalStats,
    RetrievedClause,
    build_default_llm_manager,
)
from aipaas_retrieval import (
    AuditSink,
    HashingQueryEncoder,
    InMemoryAuditSink,
    InMemoryVectorSearcher,
    JsonlAuditSink,
    RetrievalConfig,
    RetrievalFilters,
    RetrievalService,
    RetrieveRequest,
)
from query_worker.api.generation_stream import StreamRegistry
from query_worker.api.query_service import QueryService
from query_worker.graph.main_v2 import KBQueryWorkflowV2

# 进程级后台任务管理器：持有任务强引用，进程退出时统一收尾
background_tasks = BackgroundTaskManager("query-background")


def get_query_settings() -> QuerySettings:
    """获取已加载的查询服务配置。"""
    return get_settings()  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_query_workflow() -> KBQueryWorkflowV2:
    """LangGraph 编译开销大，进程内单例复用。"""
    return KBQueryWorkflowV2()


@lru_cache(maxsize=1)
def get_query_service() -> QueryService:
    """查询执行服务单例。"""
    return QueryService(
        tracker=get_task_tracker(),
        sse_hub=get_sse_hub(),
        workflow=get_query_workflow(),
        background=background_tasks,
    )


SettingsDep = Annotated[QuerySettings, Depends(get_query_settings)]
TaskTrackerDep = Annotated[TaskTracker, Depends(get_task_tracker)]
SSEHubDep = Annotated[SSEHub, Depends(get_sse_hub)]
QueryServiceDep = Annotated[QueryService, Depends(get_query_service)]


@lru_cache(maxsize=1)
def get_vector_searcher() -> InMemoryVectorSearcher:
    """#70 向量召回后端单例；P0 为内存实现，生产可替换为 Milvus 端口实现。"""
    return InMemoryVectorSearcher()


@lru_cache(maxsize=1)
def get_retrieval_audit_sink() -> AuditSink:
    """#88 审计出口：配置目录时落 JSONL，否则进程内保存（便于测试与回看）。"""
    directory = os.getenv("AIPAAS_RETRIEVAL_AUDIT_DIR")
    return JsonlAuditSink(directory) if directory else InMemoryAuditSink()


@lru_cache(maxsize=1)
def get_retrieval_service() -> RetrievalService:
    """检索服务单例：编码器维度/模型与入库侧保持一致（#44）。"""
    dimension = int(os.getenv("AIPAAS_EMBEDDING_DIM", "64"))
    model = os.getenv("AIPAAS_EMBEDDING_MODEL", "SHA256-HASH")
    timeout_ms = int(os.getenv("AIPAAS_RETRIEVAL_TIMEOUT_MS", "300"))
    return RetrievalService(
        searcher=get_vector_searcher(),
        encoder=HashingQueryEncoder(dimension, model),
        audit=get_retrieval_audit_sink(),
        config=RetrievalConfig(timeout_ms=timeout_ms),
    )


# ---------------------------------------------------------------------------
# 生成链路（#98/#103/#106/#108/#110/#114）
# ---------------------------------------------------------------------------
class RetrievalServiceAdapter:
    """检索端口适配：把 ``RetrieveHit`` 映射为生成层条款，保持生成层可独立测试。"""

    def __init__(self, service: RetrievalService) -> None:
        self.service = service

    def search(
        self,
        query: str,
        access,
        *,
        top_k: int,
        kb_id: str | None,
        kb_ids: list[str],
        filters: RetrievalFilters | None,
        trace_id: str | None,
    ) -> list[RetrievedClause]:
        request = RetrieveRequest(
            query=query,
            kb_id=kb_id,
            kb_ids=list(kb_ids),
            top_k=top_k,
            filters=filters or RetrievalFilters(),
            trace_id=trace_id,
        )
        result = self.service.retrieve(request, access)
        return [RetrievedClause.from_retrieval_hit(hit) for hit in result.hits]


@lru_cache(maxsize=1)
def get_llm_manager() -> LLMClientManager:
    """#98 双供应商 LLM 客户端单例（手动切换状态保存在注册表内）。"""
    return build_default_llm_manager()


@lru_cache(maxsize=1)
def get_generation_audit_sink() -> GenerationAuditSink:
    """生成审计出口：配置目录时落 JSONL，否则进程内保存。"""
    directory = os.getenv("AIPAAS_GENERATION_AUDIT_DIR")
    return JsonlGenerationAuditSink(directory) if directory else InMemoryGenerationAuditSink()


@lru_cache(maxsize=1)
def get_refusal_stats() -> RefusalStats:
    """#108 拒答统计单例（按原因/租户累计）。"""
    return RefusalStats()


@lru_cache(maxsize=1)
def get_generation_service() -> GenerationService:
    """生成编排服务单例：检索适配器 + 双供应商 LLM + 可配置阈值/窗口。"""
    config = GenerationConfig(
        max_input_chars=int(os.getenv("AIPAAS_MAX_INPUT_CHARS", "2000")),
        max_context_chars=int(os.getenv("AIPAAS_MAX_CONTEXT_CHARS", "6000")),
        max_segment_chars=int(os.getenv("AIPAAS_MAX_SEGMENT_CHARS", "1200")),
        max_tokens=int(os.getenv("AIPAAS_LLM_MAX_TOKENS", "1024")),
        prompt_version=os.getenv("AIPAAS_PROMPT_VERSION") or None,
        grounding_mode=os.getenv("AIPAAS_GROUNDING_MODE", "annotate"),
        refusal=RefusalPolicy(
            confidence_threshold=float(os.getenv("AIPAAS_CONFIDENCE_THRESHOLD", "0.35"))
        ),
    )
    return GenerationService(
        retriever=RetrievalServiceAdapter(get_retrieval_service()),
        llm=get_llm_manager(),
        audit=get_generation_audit_sink(),
        stats=get_refusal_stats(),
        config=config,
    )


@lru_cache(maxsize=1)
def get_generation_stream_registry() -> StreamRegistry:
    """#113 SSE 流缓冲注册表单例：支撑 Last-Event-ID 断线续传。"""
    return StreamRegistry(
        max_streams=int(os.getenv("AIPAAS_STREAM_MAX_BUFFERS", "200")),
        ttl_seconds=float(os.getenv("AIPAAS_STREAM_TTL_SECONDS", "300")),
    )
