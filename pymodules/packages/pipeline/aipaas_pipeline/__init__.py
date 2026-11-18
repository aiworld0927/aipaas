"""AIPaaS 文档解析与分块管线。

对外只暴露稳定入口：统一中间结构、解析/清洗/分块选项、注册表与编排服务。
"""

from aipaas_pipeline.annotation import (
    METADATA_RULES_VERSION,
    AnnotationService,
    MetaFilter,
    StandardMeta,
    annotate_document,
)
from aipaas_pipeline.chunking import (
    CHUNK_RULES_VERSION,
    Chunk,
    ChunkConfig,
    ChunkingInvariantError,
    assert_chunk_invariants,
    build_chunks,
    chunk_invariants,
    estimate_tokens,
)
from aipaas_pipeline.cleaning import CleaningOptions, CleaningResult, clean_document
from aipaas_pipeline.embedding import (
    EmbeddingConfig,
    EmbeddingJobService,
    HashingEncoder,
    InMemoryVectorStore,
)
from aipaas_pipeline.errors import ParseFailure
from aipaas_pipeline.models import (
    Clause,
    ParsedDocument,
    SectionNode,
    TableBlock,
    WarningItem,
)
from aipaas_pipeline.options import ParseOptions
from aipaas_pipeline.registry import ParserRegistry, get_default_registry
from aipaas_pipeline.vector_index import (
    HnswIndexService,
    InMemoryVectorIndexStore,
    IndexSpec,
)

__all__ = [
    "METADATA_RULES_VERSION",
    "AnnotationService",
    "MetaFilter",
    "StandardMeta",
    "annotate_document",
    "CHUNK_RULES_VERSION",
    "Chunk",
    "ChunkConfig",
    "ChunkingInvariantError",
    "Clause",
    "CleaningOptions",
    "CleaningResult",
    "EmbeddingConfig",
    "EmbeddingJobService",
    "HashingEncoder",
    "HnswIndexService",
    "InMemoryVectorIndexStore",
    "InMemoryVectorStore",
    "IndexSpec",
    "ParseFailure",
    "ParseOptions",
    "ParsedDocument",
    "ParserRegistry",
    "SectionNode",
    "TableBlock",
    "WarningItem",
    "assert_chunk_invariants",
    "build_chunks",
    "chunk_invariants",
    "clean_document",
    "estimate_tokens",
    "get_default_registry",
]
