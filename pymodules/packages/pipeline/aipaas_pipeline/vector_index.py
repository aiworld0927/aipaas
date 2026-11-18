"""#49 HNSW 向量索引：构建、别名热切换、失败回退/重试与全量重建。

约定：
- 集合命名与别名同时编码 **知识库 / 模型 / 索引类型**，为 P1 多模型（#45）与
  多索引类型（#50 倒排 / #51 生命周期）预留；
- 构建成功后才切换别名（双集合热切换），因此构建失败时旧索引继续提供服务；
- 每次构建记录耗时、资源采样与 chunk 数，支持失败重试、回退旧索引与全量重建。

P0 默认 ``HNSW / COSINE / 自动槽位``；``indexType/metric/params/indexSlot/alias`` 均为
创建入参并原样回显（P2 #52/#257/#260 使用）。
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from aipaas_pipeline.embedding import default_resource_sampler

INDEX_TYPE_HNSW = "HNSW"
DEFAULT_METRIC = "COSINE"
DEFAULT_PARAMS = {"M": 16, "efConstruction": 200}
SUPPORTED_METRICS = ("COSINE", "IP", "L2")
SUPPORTED_INDEX_TYPES = (INDEX_TYPE_HNSW,)
INDEX_SLOTS = ("online", "offline")

BUILDING = "BUILDING"
READY = "READY"
FAILED = "FAILED"
EMPTY = "EMPTY"


@dataclass(slots=True)
class IndexSpec:
    """索引创建参数：P0 只用 HNSW/COSINE，其余字段原样保留并回显。"""

    kb_id: str
    index_type: str | None = None
    metric: str | None = None
    params: Mapping[str, Any] | None = None
    index_slot: str | None = None
    alias: str | None = None
    model: str = "bge-m3"
    version: str | None = None
    env: str | None = None

    def __post_init__(self) -> None:
        self.kb_id = str(self.kb_id or "").strip()
        if not self.kb_id:
            raise ValueError("kbId 不能为空")
        self.index_type = str(self.index_type or INDEX_TYPE_HNSW).upper()
        if self.index_type not in SUPPORTED_INDEX_TYPES:
            raise ValueError(
                f"P0 仅支持 {SUPPORTED_INDEX_TYPES}，收到 {self.index_type}（#50/#51 预留）"
            )
        self.metric = str(self.metric or DEFAULT_METRIC).upper()
        if self.metric not in SUPPORTED_METRICS:
            raise ValueError(f"metric 仅支持 {SUPPORTED_METRICS}，收到 {self.metric}")
        merged = dict(DEFAULT_PARAMS)
        merged.update(dict(self.params or {}))
        merged["M"] = int(merged["M"])
        merged["efConstruction"] = int(merged["efConstruction"])
        if merged["M"] < 1 or merged["efConstruction"] < 1:
            raise ValueError("HNSW 参数 M/efConstruction 必须为正整数")
        self.params = merged
        self.index_slot = str(self.index_slot or "auto").lower()
        if self.index_slot != "auto" and self.index_slot not in INDEX_SLOTS:
            raise ValueError(f"indexSlot 仅支持 auto/{INDEX_SLOTS}")
        self.model = str(self.model or "bge-m3")

    def alias_name(self) -> str:
        if self.alias:
            return _safe(self.alias)
        return f"kb_{_safe(self.kb_id)}__{_model_token(self.model)}__{self.index_type.lower()}"

    def resolve_slot(self, has_active: bool) -> str:
        if self.index_slot != "auto":
            return self.index_slot
        return INDEX_SLOTS[1] if has_active else INDEX_SLOTS[0]

    def collection_name(self, slot: str, version: int) -> str:
        return f"{self.alias_name()}__{slot}__v{version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kbId": self.kb_id,
            "indexType": self.index_type,
            "metric": self.metric,
            "params": dict(self.params or {}),
            "indexSlot": self.index_slot,
            "alias": self.alias_name(),
            "model": self.model,
            "version": self.version,
            "env": self.env,
        }


class VectorIndexStore(Protocol):
    def create_collection(
        self, name: str, records: list[dict[str, Any]], *, dimension: int, metric: str,
        params: Mapping[str, Any],
    ) -> None: ...

    def drop_collection(self, name: str) -> None: ...

    def has_collection(self, name: str) -> bool: ...

    def count(self, name: str) -> int: ...

    def set_alias(self, alias: str, collection: str) -> None: ...

    def get_alias(self, alias: str) -> str | None: ...

    def search(self, collection: str, vector: Sequence[float], top_k: int) -> list[dict[str, Any]]: ...


class InMemoryVectorIndexStore:
    """内存向量索引：模拟 Milvus 集合/别名/检索语义，供离线构建与测试。"""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.RLock()

    def create_collection(
        self, name: str, records: list[dict[str, Any]], *, dimension: int, metric: str,
        params: Mapping[str, Any],
    ) -> None:
        with self._lock:
            if name in self._collections:
                raise ValueError(f"集合已存在：{name}")
            for record in records:
                vector = record.get("vector") or []
                if len(vector) != dimension:
                    raise ValueError(
                        f"chunk {record.get('chunkId')} 维度 {len(vector)} ≠ 索引维度 {dimension}"
                    )
            self._collections[name] = {
                "records": [dict(record) for record in records],
                "dimension": dimension,
                "metric": metric,
                "params": dict(params),
            }

    def drop_collection(self, name: str) -> None:
        with self._lock:
            self._collections.pop(name, None)
            for alias, target in list(self._aliases.items()):
                if target == name:
                    self._aliases.pop(alias, None)

    def has_collection(self, name: str) -> bool:
        with self._lock:
            return name in self._collections

    def count(self, name: str) -> int:
        with self._lock:
            return len((self._collections.get(name) or {}).get("records", []))

    def set_alias(self, alias: str, collection: str) -> None:
        with self._lock:
            if collection not in self._collections:
                raise ValueError(f"集合不存在，无法切换别名：{collection}")
            self._aliases[alias] = collection

    def get_alias(self, alias: str) -> str | None:
        with self._lock:
            return self._aliases.get(alias)

    def search(self, collection: str, vector: Sequence[float], top_k: int) -> list[dict[str, Any]]:
        with self._lock:
            data = self._collections.get(collection)
            if not data:
                return []
            metric = data["metric"]
            hits = []
            for record in data["records"]:
                score = _score(metric, vector, record.get("vector") or [])
                hits.append({**{k: v for k, v in record.items() if k != "vector"}, "score": round(score, 6)})
            hits.sort(key=lambda item: item["score"], reverse=metric != "L2")
            return hits[: max(1, top_k)]

    def dimension(self, collection: str) -> int | None:
        with self._lock:
            return (self._collections.get(collection) or {}).get("dimension")

    def collections(self) -> list[str]:
        with self._lock:
            return sorted(self._collections)


@dataclass(slots=True)
class IndexBuild:
    build_id: str
    kb_id: str
    spec: IndexSpec
    status: str
    total_chunks: int = 0
    indexed_chunks: int = 0
    progress: int = 0
    duration_ms: int | None = None
    collection: str | None = None
    alias: str | None = None
    previous_collection: str | None = None
    resolved_slot: str | None = None
    attempt: int = 1
    rolled_back: bool = False
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.build_id,
            "tenantId": "default",
            "kbId": self.kb_id,
            "status": self.status,
            "totalChunks": self.total_chunks,
            "indexedChunks": self.indexed_chunks,
            "progress": self.progress,
            "durationMs": self.duration_ms,
            "collection": self.collection,
            "previousCollection": self.previous_collection,
            "attempt": self.attempt,
            "rolledBack": self.rolled_back,
            "error": self.error,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "metrics": dict(self.metrics),
            **self.spec.to_dict(),
        }


ResourceSampler = Callable[[], dict[str, Any]]


class HnswIndexService:
    """HNSW 索引编排：构建完成立即切换别名；失败保留旧索引并支持重试/回退。"""

    def __init__(
        self,
        store: VectorIndexStore | None = None,
        *,
        resource_sampler: ResourceSampler | None = None,
    ) -> None:
        self.store = store or InMemoryVectorIndexStore()
        self.resource_sampler = resource_sampler or default_resource_sampler
        self._builds: dict[str, IndexBuild] = {}
        self._records: dict[str, list[dict[str, Any]]] = {}
        self._versions: dict[str, int] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 构建 / 重试 / 回退

    def build(
        self,
        kb_id: str,
        records: Sequence[Mapping[str, Any]],
        *,
        spec: IndexSpec | None = None,
        dimension: int = 1024,
        build_id: str | None = None,
    ) -> IndexBuild:
        spec = spec or IndexSpec(kb_id=kb_id, model=str((records[0] if records else {}).get("model") or "bge-m3"))
        payload = [dict(record) for record in records]
        with self._lock:
            alias = spec.alias_name()
            previous = self.store.get_alias(alias)
            slot = spec.resolve_slot(has_active=previous is not None)
            version = self._versions.get(alias, 0) + 1
            self._versions[alias] = version
            collection = spec.collection_name(slot, version)
            build = IndexBuild(
                build_id=build_id or f"idx_{uuid.uuid4().hex[:12]}",
                kb_id=kb_id,
                spec=spec,
                status=BUILDING,
                total_chunks=len(payload),
                alias=alias,
                collection=collection,
                previous_collection=previous,
                resolved_slot=slot,
                attempt=(self._builds[build_id].attempt + 1 if build_id and build_id in self._builds else 1),
                started_at=_now(),
            )
            self._builds[build.build_id] = build
            self._records[build.build_id] = payload

        started = time.perf_counter()
        try:
            self.store.create_collection(
                collection, payload, dimension=dimension, metric=spec.metric or DEFAULT_METRIC,
                params=spec.params or {},
            )
            if payload:
                self.store.set_alias(alias, collection)
            build.indexed_chunks = self.store.count(collection) if self.store.has_collection(collection) else 0
            build.status = READY if payload else EMPTY
            build.progress = 100
        except Exception as exc:
            build.status = FAILED
            build.error = f"{type(exc).__name__}: {exc}"
            build.indexed_chunks = 0
            build.progress = 0
            # 失败不动别名：旧索引继续提供服务
        finally:
            build.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            build.finished_at = _now()
            build.metrics = {
                "totalChunks": build.total_chunks,
                "indexedChunks": build.indexed_chunks,
                "durationMs": build.duration_ms,
                "chunksPerSecond": round(
                    build.indexed_chunks / max(build.duration_ms / 1000, 0.001), 3
                ),
                "resources": self.resource_sampler(),
                "attempt": build.attempt,
            }
        return build

    def get_build(self, build_id: str) -> IndexBuild:
        with self._lock:
            build = self._builds.get(build_id)
            if build is None:
                raise KeyError(f"索引构建不存在：{build_id}")
            return _copy_build(build)

    def list_builds(self, kb_id: str | None = None) -> list[IndexBuild]:
        with self._lock:
            return [_copy_build(build) for build in self._builds.values()
                    if kb_id is None or build.kb_id == kb_id]

    def retry(self, build_id: str, *, dimension: int = 1024) -> IndexBuild:
        """失败构建可重试；成功构建不重复构建（应使用全量重建）。"""
        with self._lock:
            build = self._builds.get(build_id)
            if build is None:
                raise KeyError(f"索引构建不存在：{build_id}")
            if build.status != FAILED:
                raise ValueError(f"仅 FAILED 可重试，当前 {build.status}")
            records = list(self._records.get(build_id, []))
            spec = build.spec
        return self.build(build.kb_id, records, spec=spec, dimension=dimension, build_id=build_id)

    def rollback(self, build_id: str) -> IndexBuild:
        """回退到该构建之前的旧索引（双集合热切换的反向操作）。"""
        with self._lock:
            build = self._builds.get(build_id)
            if build is None:
                raise KeyError(f"索引构建不存在：{build_id}")
            if not build.previous_collection:
                raise ValueError("该构建没有可回退的旧索引")
            if not self.store.has_collection(build.previous_collection):
                raise ValueError(f"旧集合已不存在：{build.previous_collection}")
            self.store.set_alias(build.alias or build.spec.alias_name(), build.previous_collection)
            build.rolled_back = True
            return _copy_build(build)

    def full_rebuild(
        self,
        kb_id: str,
        records: Sequence[Mapping[str, Any]],
        *,
        spec: IndexSpec | None = None,
        dimension: int = 1024,
    ) -> IndexBuild:
        """全量重建：写入新版本集合，成功后原子切换别名。"""
        return self.build(kb_id, records, spec=spec, dimension=dimension)

    # ------------------------------------------------------------------ 查询 / 状态

    def search(
        self,
        kb_id: str,
        vector: Sequence[float],
        *,
        top_k: int = 5,
        model: str = "bge-m3",
        index_type: str | None = None,
        alias: str | None = None,
    ) -> list[dict[str, Any]]:
        spec = IndexSpec(kb_id=kb_id, model=model, index_type=index_type, alias=alias)
        name = spec.alias_name()
        collection = self.store.get_alias(name)
        if collection is None:
            return []
        return self.store.search(collection, vector, top_k)

    def status(
        self,
        kb_id: str,
        *,
        model: str = "bge-m3",
        index_type: str | None = None,
        doc_count: int = 0,
        chunk_count: int | None = None,
    ) -> dict[str, Any]:
        spec = IndexSpec(kb_id=kb_id, model=model, index_type=index_type)
        alias = spec.alias_name()
        collection = self.store.get_alias(alias)
        if collection is None:
            return {
                "kbId": kb_id,
                "indexStatus": EMPTY,
                "collection": "",
                "dimension": 0,
                "docCount": doc_count,
                "chunkCount": chunk_count or 0,
                "lastBuildId": None,
                "lastBuiltAt": None,
                "alias": alias,
                "version": None,
                "env": None,
            }
        builds = [build for build in self._builds.values() if build.alias == alias]
        latest = max(builds, key=lambda item: item.started_at or "", default=None)
        dimension = getattr(self.store, "dimension", lambda _name: None)(collection) or 0
        return {
            "kbId": kb_id,
            "indexStatus": READY if self.store.count(collection) else EMPTY,
            "collection": collection,
            "dimension": dimension,
            "docCount": doc_count,
            "chunkCount": chunk_count if chunk_count is not None else self.store.count(collection),
            "lastBuildId": latest.build_id if latest else None,
            "lastBuiltAt": latest.finished_at if latest else None,
            "alias": alias,
            "version": None,
            "env": None,
        }


# --------------------------------------------------------------------------- 工具


def _score(metric: str, left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    if metric == "COSINE":
        lnorm = math.sqrt(sum(a * a for a in left)) or 1.0
        rnorm = math.sqrt(sum(b * b for b in right)) or 1.0
        return dot / (lnorm * rnorm)
    if metric == "L2":
        return -math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))
    return dot


def _model_token(model: str) -> str:
    return hashlib.sha256(model.encode("utf-8")).hexdigest()[:8]


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value or "unknown")[:64]


def _copy_build(build: IndexBuild) -> IndexBuild:
    return IndexBuild(
        build_id=build.build_id,
        kb_id=build.kb_id,
        spec=build.spec,
        status=build.status,
        total_chunks=build.total_chunks,
        indexed_chunks=build.indexed_chunks,
        progress=build.progress,
        duration_ms=build.duration_ms,
        collection=build.collection,
        alias=build.alias,
        previous_collection=build.previous_collection,
        resolved_slot=build.resolved_slot,
        attempt=build.attempt,
        rolled_back=build.rolled_back,
        error=build.error,
        started_at=build.started_at,
        finished_at=build.finished_at,
        metrics=dict(build.metrics),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
