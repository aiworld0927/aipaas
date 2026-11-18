"""AIPaaS 检索层：查询归一（#63）、向量召回（#70）、行级权限过滤（#88）。

``milvus_utils`` 保持独立模块导入（依赖 pymilvus），核心检索模型与端口不依赖向量库。
"""

from aipaas_retrieval.audit import (
    ACTION_DENIED,
    ACTION_QUERY,
    AuditSink,
    InMemoryAuditSink,
    JsonlAuditSink,
)
from aipaas_retrieval.errors import InvalidRequest, PermissionDenied, RetrievalError
from aipaas_retrieval.models import (
    RETRIEVAL_MODE_VECTOR,
    RETRIEVAL_MODES,
    RetrieveHit,
    RetrieveRequest,
    RetrieveResult,
    RetrievalFilters,
)
from aipaas_retrieval.query_understanding import (
    QueryUnderstanding,
    normalize_clause_no,
    normalize_standard_no,
    standard_no_matches,
    tokenize,
    understand_query,
)
from aipaas_retrieval.security import (
    PERMISSION_RETRIEVAL_QUERY,
    SECURITY_LEVELS,
    AccessContext,
    AccessResolver,
    StaticAccessResolver,
    can_read_level,
    level_rank,
)
from aipaas_retrieval.service import RetrievalConfig, RetrievalService
from aipaas_retrieval.store import (
    HashingQueryEncoder,
    InMemoryVectorSearcher,
    QueryEncoder,
    SearchHit,
    SearchQuery,
    VectorRecord,
    VectorSearcher,
    record_from_chunk,
)

__all__ = [
    "ACTION_DENIED",
    "ACTION_QUERY",
    "AccessContext",
    "AccessResolver",
    "AuditSink",
    "HashingQueryEncoder",
    "InMemoryAuditSink",
    "InMemoryVectorSearcher",
    "InvalidRequest",
    "JsonlAuditSink",
    "PERMISSION_RETRIEVAL_QUERY",
    "PermissionDenied",
    "QueryEncoder",
    "QueryUnderstanding",
    "RETRIEVAL_MODES",
    "RETRIEVAL_MODE_VECTOR",
    "RetrievalConfig",
    "RetrievalError",
    "RetrievalFilters",
    "RetrievalService",
    "RetrieveHit",
    "RetrieveRequest",
    "RetrieveResult",
    "SECURITY_LEVELS",
    "SearchHit",
    "SearchQuery",
    "StaticAccessResolver",
    "VectorRecord",
    "VectorSearcher",
    "can_read_level",
    "level_rank",
    "normalize_clause_no",
    "normalize_standard_no",
    "record_from_chunk",
    "standard_no_matches",
    "tokenize",
    "understand_query",
]
