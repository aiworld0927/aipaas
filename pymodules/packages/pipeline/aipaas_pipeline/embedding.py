"""#44 批量向量化任务：分批、暂停恢复、进度、维度校验、失败重试与幂等写入。

设计要点：
- 单批失败只登记失败 chunk（可定位、可重试），不中断整体任务；
- 向量维度与模型一致；写入向量库使用 upsert（按 chunkId 幂等覆盖）；
- 任务可暂停/恢复，进度与资源占用（CPU/GPU）可查询；
- 编码器与向量库均为可注入端口，P0 默认使用确定性哈希编码 + 内存库，离线可测。
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from aipaas_pipeline.chunking import Chunk

QUEUED = "QUEUED"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"


class EmbeddingEncoder(Protocol):
    """编码器端口：返回与输入等长的稠密向量列表。"""

    @property
    def dimension(self) -> int: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    """向量库写入端口：按主键 chunkId upsert，重复写入幂等。"""

    def upsert(self, collection: str, records: list[dict[str, Any]]) -> int: ...

    def dimension(self, collection: str) -> int | None: ...

    def count(self, collection: str) -> int: ...


@dataclass(slots=True)
class EmbeddingConfig:
    model: str = "SHA256-HASH"
    dimension: int = 64
    batch_size: int = 32

    def __post_init__(self) -> None:
        self.model = str(self.model or "SHA256-HASH")
        self.dimension = int(self.dimension)
        self.batch_size = int(self.batch_size)
        if self.dimension < 1:
            raise ValueError("dimension 必须为正整数")
        if not 1 <= self.batch_size <= 512:
            raise ValueError("batch_size 必须在 1..512 之间")

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "dimension": self.dimension, "batchSize": self.batch_size}


class HashingEncoder:
    """确定性编码器：由文本 sha256 派生固定维度向量，用于离线测试与维度校验。"""

    def __init__(self, dimension: int = 64, model: str = "SHA256-HASH") -> None:
        self._dimension = int(dimension)
        self.model = model

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256((text or "").encode("utf-8")).digest()
            raw = (digest * (self._dimension // len(digest) + 1))[: self._dimension]
            vector = [value / 255.0 - 0.5 for value in raw]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class InMemoryVectorStore:
    """内存向量库：按 (collection, chunkId) upsert，记录维度供校验。"""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}
        self._dimensions: dict[str, int] = {}
        self._lock = threading.RLock()

    def upsert(self, collection: str, records: list[dict[str, Any]]) -> int:
        with self._lock:
            bucket = self._collections.setdefault(collection, {})
            for record in records:
                bucket[str(record["chunkId"])] = dict(record)
                if record.get("vector"):
                    self._dimensions[collection] = len(record["vector"])
            return len(bucket)

    def dimension(self, collection: str) -> int | None:
        with self._lock:
            return self._dimensions.get(collection)

    def count(self, collection: str) -> int:
        with self._lock:
            return len(self._collections.get(collection, {}))

    def get(self, collection: str, chunk_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._collections.get(collection, {}).get(str(chunk_id))
            return dict(record) if record else None

    def all(self, collection: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._collections.get(collection, {}).values()]


@dataclass(slots=True)
class EmbeddingJob:
    job_id: str
    kb_id: str
    document_ids: list[str]
    config: EmbeddingConfig
    status: str = QUEUED
    total: int = 0
    processed: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    idempotency_key: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def progress(self) -> int:
        if not self.total:
            return 0
        return min(100, round((self.processed + self.failed) * 100 / self.total))

    @property
    def success_rate(self) -> float:
        if not self.total:
            return 100.0
        return round(self.processed * 100 / self.total, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.job_id,
            "tenantId": "default",
            "kbId": self.kb_id,
            "documentIds": list(self.document_ids),
            "status": self.status,
            "model": self.config.model,
            "dimension": self.config.dimension,
            "batchSize": self.config.batch_size,
            "total": self.total,
            "processed": self.processed,
            "failed": self.failed,
            "progress": self.progress,
            "successRate": self.success_rate,
            "failures": [dict(item) for item in self.failures],
            "error": self.error,
            "metrics": dict(self.metrics),
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }


@dataclass(slots=True)
class _Item:
    chunk_id: str
    document_id: str
    kb_id: str
    text: str
    hash: str


ResourceSampler = Callable[[], dict[str, Any]]


def default_resource_sampler() -> dict[str, Any]:
    """采样 CPU/GPU 占用；依赖缺失时返回 None 值，不阻塞任务。"""
    sample: dict[str, Any] = {"cpuPercent": None, "gpuUtilization": None, "source": "unavailable"}
    try:
        import psutil  # type: ignore

        sample["cpuPercent"] = float(psutil.cpu_percent(interval=None))
        sample["source"] = "psutil"
    except Exception:
        pass
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            sample["gpuUtilization"] = float(torch.cuda.utilization())
            sample["source"] = "psutil+torch" if sample["cpuPercent"] is not None else "torch"
    except Exception:
        pass
    return sample


class EmbeddingJobService:
    """向量化任务编排（同步执行，可由调用方放入线程池）。"""

    def __init__(
        self,
        encoder: EmbeddingEncoder,
        store: VectorStore,
        config: EmbeddingConfig | None = None,
        *,
        resource_sampler: ResourceSampler | None = None,
    ) -> None:
        self.encoder = encoder
        self.store = store
        self.config = config or EmbeddingConfig(dimension=encoder.dimension, model=getattr(encoder, "model", "model"))
        if int(encoder.dimension) != self.config.dimension:
            raise ValueError(
                f"编码器维度 {encoder.dimension} 与配置维度 {self.config.dimension} 不一致"
            )
        self.resource_sampler = resource_sampler or default_resource_sampler
        self._jobs: dict[str, EmbeddingJob] = {}
        self._pending: dict[str, list[_Item]] = {}
        self._retry_pending: dict[str, list[_Item]] = {}
        self._failed_items: dict[str, dict[str, _Item]] = {}
        self._index: dict[str, str] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 任务生命周期

    def create_job(
        self,
        kb_id: str,
        chunks: Sequence[Chunk | Mapping[str, Any]],
        *,
        document_ids: Iterable[str] | None = None,
        idempotency_key: str | None = None,
        model: str | None = None,
    ) -> EmbeddingJob:
        if not kb_id:
            raise ValueError("kbId 不能为空")
        with self._lock:
            if idempotency_key and idempotency_key in self._index:
                return self.get_job(self._index[idempotency_key])
            items = [_item_from(chunk, kb_id) for chunk in chunks]
            config = EmbeddingConfig(
                model=model or self.config.model,
                dimension=self.config.dimension,
                batch_size=self.config.batch_size,
            )
            existing_dim = self.store.dimension(_collection(kb_id, config.model))
            if existing_dim is not None and existing_dim != config.dimension:
                raise ValueError(
                    f"集合维度 {existing_dim} 与模型 {config.model} 维度 {config.dimension} 不一致"
                )
            job = EmbeddingJob(
                job_id=f"emb_{uuid.uuid4().hex[:12]}",
                kb_id=kb_id,
                document_ids=sorted({item.document_id for item in items} if document_ids is None
                                    else set(document_ids)),
                config=config,
                total=len(items),
                idempotency_key=idempotency_key,
                created_at=_now(),
            )
            self._jobs[job.job_id] = job
            self._pending[job.job_id] = items
            if idempotency_key:
                self._index[idempotency_key] = job.job_id
            return _copy(job)

    def get_job(self, job_id: str) -> EmbeddingJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"向量化任务不存在：{job_id}")
            return _copy(job)

    def list_jobs(self) -> list[EmbeddingJob]:
        with self._lock:
            return [_copy(job) for job in self._jobs.values()]

    def pause(self, job_id: str) -> EmbeddingJob:
        with self._lock:
            job = self._jobs[job_id]
            if job.status not in (QUEUED, RUNNING):
                raise ValueError(f"仅 QUEUED/RUNNING 可暂停，当前 {job.status}")
            job.status = PAUSED
            return _copy(job)

    def resume(self, job_id: str) -> EmbeddingJob:
        with self._lock:
            job = self._jobs[job_id]
            if job.status != PAUSED:
                raise ValueError(f"仅 PAUSED 可恢复，当前 {job.status}")
            job.status = RUNNING
        self.run(job_id)
        return self.get_job(job_id)

    def run(self, job_id: str, max_batches: int | None = None) -> EmbeddingJob:
        """执行任务；``max_batches`` 便于测试与分批推进。"""
        with self._lock:
            job = self._jobs[job_id]
            if job.status in (SUCCEEDED,):
                return _copy(job)
            if job.status == PAUSED:
                return _copy(job)
            job.status = RUNNING
            job.started_at = job.started_at or _now()
            pending = self._pending.get(job_id, [])
            retry = self._retry_pending.get(job_id, [])
            batch_size = job.config.batch_size

        started = time.perf_counter()
        batches = 0
        samples: list[dict[str, Any]] = []
        work: list[_Item] = retry or pending
        consumed = 0
        while consumed < len(work):
            with self._lock:
                current = self._jobs[job_id]
                if current.status == PAUSED:
                    samples.append(self.resource_sampler())
                    current.metrics.update(_metrics(samples, batches, started, current))
                    return _copy(current)
            batch = work[consumed: consumed + batch_size]
            consumed += batch_size
            batches += 1
            self._process_batch(job_id, batch)
            samples.append(self.resource_sampler())
            if max_batches is not None and batches >= max_batches and consumed < len(work):
                with self._lock:
                    current = self._jobs[job_id]
                    if retry:
                        self._retry_pending[job_id] = work[consumed:]
                    else:
                        self._pending[job_id] = work[consumed:]
                    current.metrics.update(_metrics(samples, batches, started, current))
                    current.status = PAUSED
                    return _copy(current)

        with self._lock:
            current = self._jobs[job_id]
            if retry and consumed >= len(work):
                self._retry_pending.pop(job_id, None)
            if not retry:
                self._pending[job_id] = []
            current.finished_at = _now()
            if current.processed == 0 and current.failed > 0:
                current.status = FAILED
                current.error = current.error or "全部 chunk 向量化失败"
            elif current.failed > 0:
                # 部分失败仍视为完成，失败明细可定位并支持重试
                current.status = SUCCEEDED
            else:
                current.status = SUCCEEDED
            current.metrics.update(_metrics(samples, batches, started, current))
            return _copy(current)

    def retry_failures(self, job_id: str) -> EmbeddingJob:
        """只重试失败 chunk：重置失败明细，成功后失败数下降。"""
        with self._lock:
            job = self._jobs[job_id]
            if not job.failures:
                return _copy(job)
            failed_ids = {item["chunkId"] for item in job.failures}
            items = [item for item in self._pending.get(job_id, []) if item.chunk_id in failed_ids]
            items += [item for item in self._retry_pending.get(job_id, []) if item.chunk_id in failed_ids]
            if not items:
                # 失败明细只保留截断文本，重试使用任务内部保存的完整原文
                items = [item for chunk_id, item in self._failed_items.get(job_id, {}).items()
                         if chunk_id in failed_ids]
            job.failures = []
            job.failed = max(0, job.failed - len(failed_ids))
            job.status = RUNNING
            job.finished_at = None
            self._retry_pending[job_id] = items
            self._pending[job_id] = [item for item in self._pending.get(job_id, [])
                                     if item.chunk_id not in failed_ids]
        result = self.run(job_id)
        with self._lock:
            still_failed = {item["chunkId"] for item in result.failures}
            registry = self._failed_items.get(job_id, {})
            for chunk_id in list(registry):
                if chunk_id not in still_failed:
                    registry.pop(chunk_id, None)
        return result

    def verify_vectors(self, job_id: str) -> dict[str, Any]:
        """校验已写入向量维度与模型一致。"""
        job = self.get_job(job_id)
        collection = _collection(job.kb_id, job.config.model)
        records = getattr(self.store, "all", lambda _name: [])(collection)
        bad = [r["chunkId"] for r in records if len(r.get("vector") or []) != job.config.dimension]
        return {
            "jobId": job_id,
            "collection": collection,
            "model": job.config.model,
            "expectedDimension": job.config.dimension,
            "stored": len(records),
            "mismatched": bad,
            "dimensionValid": not bad,
        }

    # ------------------------------------------------------------------ 内部

    def _process_batch(self, job_id: str, batch: list[_Item]) -> None:
        texts = [item.text for item in batch]
        try:
            vectors = self.encoder.encode(texts)
            if len(vectors) != len(batch):
                raise ValueError(f"编码器返回 {len(vectors)} 条向量，期望 {len(batch)} 条")
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                for item in batch:
                    self._register_failure(job_id, job, item, "EMBED.BATCH_FAILED",
                                           f"{type(exc).__name__}: {exc}", True)
            return

        records: list[dict[str, Any]] = []
        failures: list[tuple[_Item, str, str, bool]] = []
        for item, vector in zip(batch, vectors):
            if not isinstance(vector, (list, tuple)) or len(vector) != self.encoder.dimension:
                failures.append(
                    (item, "EMBED.DIM_MISMATCH",
                     f"维度 {len(vector) if hasattr(vector, '__len__') else 'unknown'} "
                     f"≠ 期望 {self.encoder.dimension}", False)
                )
                continue
            records.append(
                {
                    "chunkId": item.chunk_id,
                    "documentId": item.document_id,
                    "kbId": item.kb_id,
                    "text": item.text,
                    "hash": item.hash,
                    "vector": list(vector),
                    "model": self._jobs[job_id].config.model,
                    "dimension": len(vector),
                }
            )
        if records:
            try:
                self.store.upsert(_collection(self._jobs[job_id].kb_id, self._jobs[job_id].config.model), records)
            except Exception as exc:
                with self._lock:
                    job = self._jobs[job_id]
                    for item in batch:
                        self._register_failure(job_id, job, item, "EMBED.WRITE_FAILED",
                                               f"{type(exc).__name__}: {exc}", True)
                return
        with self._lock:
            job = self._jobs[job_id]
            job.processed += len(records)
            for item, code, message, retryable in failures:
                self._register_failure(job_id, job, item, code, message, retryable)

    def _register_failure(
        self, job_id: str, job: EmbeddingJob, item: _Item, code: str, message: str, retryable: bool
    ) -> None:
        """记录失败明细，并保存完整原文供重试（对外仅暴露截断文本）。"""
        _fail(job, item, code, message, retryable)
        self._failed_items.setdefault(job_id, {})[item.chunk_id] = item


def _item_from(chunk: Chunk | Mapping[str, Any], kb_id: str) -> _Item:
    if isinstance(chunk, Chunk):
        return _Item(chunk.chunk_id, chunk.document_id, kb_id, chunk.content, chunk.content_hash)
    payload = dict(chunk)
    content = str(payload.get("content") or payload.get("text") or "")
    return _Item(
        chunk_id=str(payload.get("chunkId") or payload.get("id") or ""),
        document_id=str(payload.get("docId") or payload.get("documentId") or ""),
        kb_id=kb_id,
        text=content,
        hash=str(payload.get("hash") or hashlib.sha256(content.encode("utf-8")).hexdigest()),
    )


def _fail(job: EmbeddingJob, item: _Item, code: str, message: str, retryable: bool) -> None:
    job.failed += 1
    job.failures.append(
        {
            "chunkId": item.chunk_id,
            "documentId": item.document_id,
            "code": code,
            "message": message,
            "retryable": retryable,
            "text": item.text[:120],
            "hash": item.hash,
        }
    )


def _metrics(samples: list[dict[str, Any]], batches: int, started: float, job: EmbeddingJob) -> dict[str, Any]:
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    previous = job.metrics or {}
    total_duration_ms = round(float(previous.get("durationMs", 0) or 0) + duration_ms, 3)
    total_batches = int(previous.get("batchCount", 0) or 0) + batches
    total_samples = int((previous.get("resources") or {}).get("samples", 0) or 0) + len(samples)
    duration_s = max(total_duration_ms / 1000, 0.001)
    cpu = [s["cpuPercent"] for s in samples if s.get("cpuPercent") is not None]
    gpu = [s["gpuUtilization"] for s in samples if s.get("gpuUtilization") is not None]
    return {
        "model": job.config.model,
        "dimension": job.config.dimension,
        "batchSize": job.config.batch_size,
        "batchCount": total_batches,
        "durationMs": total_duration_ms,
        "chunksPerSecond": round(job.processed / duration_s, 3),
        "successRate": job.success_rate,
        "resources": {
            "cpuPercent": round(sum(cpu) / len(cpu), 2) if cpu else (previous.get("resources") or {}).get(
                "cpuPercent"
            ),
            "gpuUtilization": round(sum(gpu) / len(gpu), 2) if gpu else (previous.get("resources") or {}).get(
                "gpuUtilization"
            ),
            "samples": total_samples,
        },
    }


def _collection(kb_id: str, model: str) -> str:
    token = hashlib.sha256(f"{model}".encode("utf-8")).hexdigest()[:8]
    return f"kb_{_safe(kb_id)}__embed__{token}"


def _copy(job: EmbeddingJob) -> EmbeddingJob:
    return EmbeddingJob(
        job_id=job.job_id,
        kb_id=job.kb_id,
        document_ids=list(job.document_ids),
        config=job.config,
        status=job.status,
        total=job.total,
        processed=job.processed,
        failed=job.failed,
        failures=[dict(item) for item in job.failures],
        error=job.error,
        idempotency_key=job.idempotency_key,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        metrics=dict(job.metrics),
    )


def _safe(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._-]", "_", value or "unknown")[:64]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
