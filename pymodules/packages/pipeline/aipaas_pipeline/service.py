"""解析编排：注册表 → 解析器 → 自动重试 → 版本化产物 → 幂等分块。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aipaas_common.core.observability import span
from aipaas_pipeline.annotation import (
    AnnotationService,
    FileAnnotationStore,
    MetaFilter,
)
from aipaas_pipeline.chunking import (
    CHUNK_RULES_VERSION,
    ChunkConfig,
    ChunkStore,
    FileChunkStore,
    InMemoryChunkStore,
    assert_chunk_invariants,
    build_chunks,
    chunk_invariants,
)
from aipaas_pipeline.cleaning import CleaningOptions, CleaningResult, clean_document
from aipaas_pipeline.context import ParseContext
from aipaas_pipeline.errors import FailureCodes, ParseFailure
from aipaas_pipeline.models import ParsedDocument, WarningItem
from aipaas_pipeline.options import ParseOptions
from aipaas_pipeline.registry import ParserRegistry, get_default_registry
from aipaas_pipeline.repository import (
    FileParseResultRepository,
    InMemoryParseResultRepository,
    ParseRecord,
    ParseResultRepository,
)
from aipaas_pipeline.storage import ArtifactStore, InMemoryArtifactStore, json_bytes

logger = logging.getLogger(__name__)


def default_local_root() -> Path:
    import os

    configured = os.getenv("AIPAAS_PARSE_DATA_DIR")
    if configured:
        return Path(configured)
    return Path.cwd() / "output_files" / "parse-data"


class ParseService:
    """线程安全的解析编排服务。"""

    def __init__(
        self,
        registry: ParserRegistry | None = None,
        artifacts: ArtifactStore | None = None,
        chunks: ChunkStore | None = None,
        results: ParseResultRepository | None = None,
        annotations: AnnotationService | None = None,
    ) -> None:
        self.registry = registry or get_default_registry()
        if artifacts is None or chunks is None or results is None:
            root = default_local_root()
            artifacts = artifacts or InMemoryArtifactStore()
            chunks = chunks or FileChunkStore(root / "chunks")
            results = results or FileParseResultRepository(root / "results")
        self.artifacts = artifacts
        self.chunks = chunks
        self.results = results
        self.annotations = annotations or AnnotationService()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ 公开 API

    def parse(
        self,
        data: bytes,
        *,
        doc_id: str,
        tenant_id: str = "default",
        filename: str = "",
        fmt: str | None = None,
        standard_no: str | None = None,
        options: ParseOptions | None = None,
        clean_options: CleaningOptions | None = None,
        chunk_options: ChunkConfig | None = None,
    ) -> ParsedDocument:
        if not doc_id:
            raise ValueError("doc_id 不能为空")
        resolved_format = (fmt or Path(filename).suffix).lstrip(".").lower()
        options = options or ParseOptions()
        chunk_config = chunk_options or ChunkConfig()
        ctx = ParseContext(
            doc_id=doc_id,
            tenant_id=tenant_id,
            filename=filename,
            format=resolved_format,
            standard_no_hint=standard_no,
            options=options,
        )

        document = self._parse_with_retry(data, ctx, resolved_format, options)
        with self._document_lock(doc_id):
            version = self.results.next_version(doc_id)
            document.version = version
            cleaning: CleaningResult | None = None
            chunk_result: list[dict[str, Any]] = []
            chunk_meta: dict[str, Any] = {}
            if document.success:
                # 清洗只产出“新”产物，原始解析结果保持不动；分块使用清洗后文本。
                with span("parse.clean", documentId=doc_id, version=version):
                    cleaning = clean_document(document, clean_options)
                cleaning.document.version = version
                with span("parse.chunk", documentId=doc_id, version=version):
                    chunks = build_chunks(cleaning.document, chunk_config)
                with span("parse.metadata", documentId=doc_id, version=version):
                    standard_meta = self.annotations.annotate_and_save(
                        cleaning.document, actor="system", reason="parse"
                    )
                    self.annotations.decorate_chunks(cleaning.document, chunks, standard_meta)
                with span("parse.chunk.index", documentId=doc_id, version=version):
                    chunk_meta = assert_chunk_invariants(cleaning.document, chunks, chunk_config)
                    count = self.chunks.replace_document(doc_id, chunks)
                    chunk_result = [chunk.to_dict() for chunk in chunks]
                keys = sorted(chunk.to_dict()["chunkId"] for chunk in chunks)
                document.extras["chunkCount"] = count
                document.extras["chunkStrategy"] = chunk_config.strategy
                document.extras["chunkConfigVersion"] = chunk_config.config_version
                document.extras["chunkClauseCount"] = chunk_meta["clauseChunks"]
                document.extras["chunkCountMatchesClauses"] = chunk_meta["countMatchesClauses"]
                document.extras["chunkIdsDigest"] = hashlib.sha256(
                    "\n".join(keys).encode("utf-8")
                ).hexdigest()
            payload = document.to_payload()
            artifact_key = f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/v{version}/parse-result.json"
            self.artifacts.put_bytes(artifact_key, json_bytes(payload))
            manifest: dict[str, Any] = {
                "documentId": doc_id,
                "tenantId": tenant_id,
                "version": version,
                "parser": document.parser,
                "success": document.success,
                "failureCode": document.failure_code,
                "standardNo": document.standard_no,
                "pages": document.pages,
                "clauseCount": len(document.clauses),
                "tableCount": len(document.tables),
                "chunkCount": document.extras.get("chunkCount", 0),
                "chunkIdsDigest": document.extras.get("chunkIdsDigest"),
                "chunkStrategy": document.extras.get("chunkStrategy"),
                "chunkConfigVersion": document.extras.get("chunkConfigVersion"),
                "chunkClauseCount": document.extras.get("chunkClauseCount"),
                "chunkCountMatchesClauses": document.extras.get("chunkCountMatchesClauses"),
                "artifactKey": artifact_key,
                "parsedAt": payload["parsedAt"],
            }
            record_extra: dict[str, Any] = {}
            if document.success and chunk_meta:
                config_key = f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/v{version}/chunk-config.json"
                chunks_key = f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/v{version}/chunks.json"
                self.artifacts.put_bytes(
                    config_key,
                    json_bytes(
                        {
                            "documentId": doc_id,
                            "version": version,
                            "rulesVersion": CHUNK_RULES_VERSION,
                            "configVersion": chunk_config.config_version,
                            "config": chunk_config.to_dict(),
                            "assertions": chunk_meta,
                        }
                    ),
                )
                self.artifacts.put_bytes(
                    chunks_key,
                    json_bytes(
                        {
                            "documentId": doc_id,
                            "version": version,
                            "configVersion": chunk_config.config_version,
                            "config": chunk_config.to_dict(),
                            "chunkCount": len(chunk_result),
                            "chunks": chunk_result,
                        }
                    ),
                )
                self.artifacts.put_bytes(
                    f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/latest-chunk.json",
                    json_bytes(
                        {
                            "version": version,
                            "configVersion": chunk_config.config_version,
                            "chunkConfigKey": config_key,
                            "chunksKey": chunks_key,
                            "chunkCount": len(chunk_result),
                        }
                    ),
                )
                manifest["chunkConfigKey"] = config_key
                manifest["chunksKey"] = chunks_key
                manifest["chunkAssertions"] = chunk_meta
                record_extra.update(
                    {
                        "chunkConfigKey": config_key,
                        "chunksKey": chunks_key,
                        "chunkConfigVersion": chunk_config.config_version,
                    }
                )
            if document.success:
                metadata_key = f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/v{version}/metadata.json"
                self.artifacts.put_bytes(metadata_key, json_bytes(standard_meta))
                self.artifacts.put_bytes(
                    f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/latest-metadata.json",
                    json_bytes(
                        {
                            "version": version,
                            "metadataKey": metadata_key,
                            "confidence": standard_meta.get("confidence"),
                            "source": standard_meta.get("source"),
                        }
                    ),
                )
                document.extras["metadataKey"] = metadata_key
                document.extras["metadataConfidence"] = standard_meta.get("confidence")
                document.extras["metadataSource"] = standard_meta.get("source")
                record_extra.update(
                    {
                        "metadataKey": metadata_key,
                        "metadataConfidence": standard_meta.get("confidence"),
                    }
                )
            if cleaning is not None:
                clean_result_key = f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/v{version}/clean-result.json"
                clean_diff_key = f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/v{version}/clean-diff.json"
                cleaning.document.version = version
                cleaning.document.extras["cleanDiffKey"] = clean_diff_key
                self.artifacts.put_bytes(clean_result_key, json_bytes(cleaning.clean_payload()))
                self.artifacts.put_bytes(clean_diff_key, json_bytes(cleaning.diff))
                self.artifacts.put_bytes(
                    f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/latest-clean.json",
                    json_bytes(
                        {
                            "version": version,
                            "cleanResultKey": clean_result_key,
                            "cleanDiffKey": clean_diff_key,
                            "summary": cleaning.diff["summary"],
                        }
                    ),
                )
                manifest["cleaned"] = True
                manifest["cleanResultKey"] = clean_result_key
                manifest["cleanDiffKey"] = clean_diff_key
                manifest["cleanSummary"] = cleaning.diff["summary"]
                record_extra.update(
                    {
                        "cleanResultKey": clean_result_key,
                        "cleanDiffKey": clean_diff_key,
                        "cleanSummary": cleaning.diff["summary"],
                    }
                )
            self.artifacts.put_bytes(
                f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/v{version}/manifest.json", json_bytes(manifest)
            )
            self.artifacts.put_bytes(
                f"parse/{_safe(tenant_id)}/{_safe(doc_id)}/latest.json",
                json_bytes({"version": version, "artifactKey": artifact_key, "success": document.success}),
            )
            self.results.save(
                ParseRecord(
                    document_id=doc_id,
                    version=version,
                    parser=document.parser,
                    success=document.success,
                    artifact_key=artifact_key,
                    payload=payload,
                    parsed_at=document.parsed_at,
                    tenant_id=tenant_id,
                    failure_code=document.failure_code,
                    pages=document.pages,
                    chunk_count=int(document.extras.get("chunkCount", 0) or 0),
                    extra=record_extra,
                )
            )
        logger.info(
            "解析完成 document=%s version=%s parser=%s success=%s clauses=%s tables=%s warnings=%s",
            doc_id,
            document.version,
            document.parser,
            document.success,
            len(document.clauses),
            len(document.tables),
            len(document.warnings),
        )
        if cleaning is not None:
            logger.info(
                "清洗完成 document=%s version=%s changedFields=%s removedChars=%s "
                "clauseNumbersPreserved=%s",
                doc_id,
                version,
                cleaning.diff["summary"].get("changedFields"),
                cleaning.diff["summary"].get("removedChars"),
                cleaning.diff["invariants"].get("clauseNumbersPreserved"),
            )
        if chunk_meta:
            logger.info(
                "分块完成 document=%s version=%s strategy=%s configVersion=%s chunks=%s "
                "clauseChunks=%s clauseCount=%s countMatches=%s",
                doc_id,
                version,
                chunk_meta["strategy"],
                chunk_meta["configVersion"],
                chunk_meta["totalChunks"],
                chunk_meta["clauseChunks"],
                chunk_meta["clauseCount"],
                chunk_meta["countMatchesClauses"],
            )
        return document

    def get_clean_result(self, doc_id: str) -> dict[str, Any] | None:
        return self._read_clean_artifact(doc_id, "cleanResultKey")

    def get_clean_diff(self, doc_id: str) -> dict[str, Any] | None:
        return self._read_clean_artifact(doc_id, "cleanDiffKey")

    def get_chunk_config(self, doc_id: str) -> dict[str, Any] | None:
        return self._read_clean_artifact(doc_id, "chunkConfigKey")

    def get_chunks_artifact(self, doc_id: str) -> dict[str, Any] | None:
        return self._read_clean_artifact(doc_id, "chunksKey")

    # ------------------------------------------------------------------ #57 元数据

    def get_metadata(self, doc_id: str) -> dict[str, Any] | None:
        return self.annotations.get(doc_id)

    def update_metadata(
        self, doc_id: str, patch: dict[str, Any], *, actor: str = "system", reason: str = "manual_correction"
    ) -> dict[str, Any]:
        return self.annotations.update(doc_id, patch, actor=actor, reason=reason)

    def query_metadata(self, meta_filter: MetaFilter | None = None) -> list[dict[str, Any]]:
        return self.annotations.query(meta_filter)

    def metadata_audit(self, doc_id: str | None = None) -> list[dict[str, Any]]:
        return self.annotations.audit(doc_id)

    def list_parse_records(self) -> list[ParseRecord]:
        """返回已登记文档的最新解析记录（供向量化任务按文档集合取 chunk）。"""
        records = getattr(self.results, "list_latest", None)
        if records is not None:
            return list(records())
        return []

    def preview_chunks(
        self, doc_id: str, config: ChunkConfig | None = None, *, limit: int = 50
    ) -> dict[str, Any] | None:
        """按指定参数预览分块，不写任何产物；与正式分块共用同一构建函数。"""
        document = self._load_document(doc_id)
        if document is None:
            return None
        chunk_config = config or ChunkConfig()
        chunks = build_chunks(document, chunk_config)
        invariants = chunk_invariants(document, chunks, chunk_config)
        limit = max(1, min(int(limit), 500))
        preview = chunks[:limit]
        return {
            "documentId": doc_id,
            "version": document.version,
            "config": chunk_config.to_dict(),
            "configVersion": chunk_config.config_version,
            "clauseCount": invariants["clauseCount"],
            "clauseChunks": invariants["clauseChunks"],
            "totalChunks": len(chunks),
            "truncated": len(chunks) > len(preview),
            "invariants": invariants,
            "chunks": [chunk.to_dict() for chunk in preview],
        }

    def get_document(self, doc_id: str) -> ParsedDocument | None:
        """读取最新清洗产物（无清洗产物时回退原始解析结果）。"""
        payload = self.get_clean_result(doc_id)
        if payload is None:
            payload = self.get_result(doc_id)
        if payload is None:
            return None
        return ParsedDocument.from_payload(payload)

    def _load_document(self, doc_id: str) -> ParsedDocument | None:
        return self.get_document(doc_id)

    def list_clauses(
        self, doc_id: str, *, page: int = 1, page_size: int = 100
    ) -> dict[str, Any] | None:
        """条目化条款清单（#404/#406 复用）：含章节、页码与对应 chunkId。"""
        document = self.get_document(doc_id)
        if document is None:
            return None
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 500))
        chunk_by_clause: dict[str, str] = {}
        chunk_payload = self.get_chunks_artifact(doc_id) or {}
        for chunk in chunk_payload.get("chunks") or []:
            clause_no = chunk.get("clauseNo")
            chunk_id = chunk.get("chunkId")
            if clause_no and chunk_id and clause_no not in chunk_by_clause:
                chunk_by_clause[str(clause_no)] = str(chunk_id)
        items = [
            {
                "tenantId": document.tenant_id or "default",
                "clauseNo": clause.clause_no,
                "title": clause.title,
                "chapterPath": clause.chapter_path,
                "text": clause.text,
                "pageNo": clause.page_no,
                "level": clause.level,
                "chunkId": chunk_by_clause.get(clause.clause_no),
            }
            for clause in document.clauses
        ]
        start = (page - 1) * page_size
        return {
            "documentId": doc_id,
            "version": document.version,
            "clauseCount": len(items),
            "items": items[start : start + page_size],
            "total": len(items),
            "page": page,
            "pageSize": page_size,
        }

    def _read_clean_artifact(self, doc_id: str, key: str) -> dict[str, Any] | None:
        record = self.results.latest(doc_id)
        if record is None:
            return None
        artifact_key = (record.extra or {}).get(key)
        if not artifact_key:
            return None
        data = self.artifacts.get_bytes(str(artifact_key))
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    def get_result(self, doc_id: str) -> dict[str, Any] | None:
        record = self.results.latest(doc_id)
        return record.payload if record else None

    def get_record(self, doc_id: str) -> ParseRecord | None:
        return self.results.latest(doc_id)

    def list_versions(self, doc_id: str) -> list[int]:
        return self.results.list_versions(doc_id)

    def chunk_count(self, doc_id: str) -> int:
        return self.chunks.count(doc_id)

    # ------------------------------------------------------------------ 内部

    def _parse_with_retry(
        self, data: bytes, ctx: ParseContext, fmt: str, options: ParseOptions
    ) -> ParsedDocument:
        parser_name = fmt or "unknown"
        try:
            parser_name = self.registry.get(fmt).name
        except ParseFailure:
            pass
        attempt = 0
        last_failure: ParseFailure | None = None
        while True:
            try:
                document = self.registry.parse(fmt, data, ctx)
                document.retry_count = attempt
                return document
            except ParseFailure as exc:
                last_failure = exc
                logger.warning(
                    "解析失败 document=%s format=%s attempt=%s retryable=%s code=%s page=%s element=%s: %s",
                    ctx.doc_id,
                    fmt,
                    attempt,
                    exc.retryable,
                    exc.code,
                    exc.page_no,
                    exc.element,
                    exc.message,
                )
                if exc.retryable and attempt < options.max_retries:
                    attempt += 1
                    if options.retry_backoff_seconds > 0:
                        time.sleep(options.retry_backoff_seconds * attempt)
                    continue
                return self._failure_document(ctx, parser_name, exc, attempt, fmt)
            except Exception as exc:  # 不得静默吞异常：完整记录堆栈后再重试/失败
                last_failure = ParseFailure(
                    FailureCodes.INTERNAL,
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    retryable=True,
                )
                logger.exception(
                    "解析器内部异常 document=%s format=%s attempt=%s", ctx.doc_id, fmt, attempt
                )
                if attempt < options.max_retries:
                    attempt += 1
                    if options.retry_backoff_seconds > 0:
                        time.sleep(options.retry_backoff_seconds * attempt)
                    continue
                return self._failure_document(ctx, parser_name, last_failure, attempt, fmt)

    @staticmethod
    def _failure_document(
        ctx: ParseContext, parser_name: str, failure: ParseFailure, retry_count: int, fmt: str
    ) -> ParsedDocument:
        warnings = list(failure.warnings)
        warnings.append(
            WarningItem(
                failure.code,
                failure.message,
                page_no=failure.page_no,
                element=failure.element,
            )
        )
        return ParsedDocument(
            doc_id=ctx.doc_id,
            tenant_id=ctx.tenant_id,
            parser=parser_name,
            success=False,
            title="",
            standard_no=ctx.standard_no_hint,
            chapter_tree=[],
            clauses=[],
            tables=[],
            raw_text="",
            warnings=warnings,
            pages=0,
            failure_code=failure.code,
            failure_message=failure.message,
            retry_count=retry_count,
            parsed_at=datetime.now(timezone.utc),
            extras={
                "failurePageNo": failure.page_no,
                "failureElement": failure.element,
                "failureRetryable": failure.retryable,
                "format": fmt,
            },
        )

    def _document_lock(self, doc_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(doc_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[doc_id] = lock
            return lock


def build_default_service() -> ParseService:
    """按环境变量装配默认服务（本地目录 + 内存产物缓存）。"""
    import os

    root = default_local_root()
    backend = os.getenv("AIPAAS_PARSE_ARTIFACT_BACKEND", "local").lower()
    artifacts: ArtifactStore
    if backend == "minio":
        from aipaas_pipeline.storage import MinioArtifactStore

        artifacts = MinioArtifactStore(os.getenv("AIPAAS_PARSE_BUCKET", "aipaas-parse"))
    elif backend == "memory":
        artifacts = InMemoryArtifactStore()
    else:
        from aipaas_pipeline.storage import LocalArtifactStore

        artifacts = LocalArtifactStore(root / "artifacts")
    return ParseService(
        artifacts=artifacts,
        chunks=FileChunkStore(root / "chunks"),
        results=FileParseResultRepository(root / "results"),
        annotations=AnnotationService(FileAnnotationStore(root / "metadata")),
    )


def _safe(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._-]", "_", value or "unknown")[:128]
