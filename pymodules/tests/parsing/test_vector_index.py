"""#49 HNSW 索引：构建生效、失败保留旧索引、重试/回退、全量重建、命名契约与监控。"""
from __future__ import annotations

import pytest

from aipaas_pipeline.vector_index import (
    DEFAULT_PARAMS,
    EMPTY,
    FAILED,
    READY,
    HnswIndexService,
    InMemoryVectorIndexStore,
    IndexSpec,
)


def records(count: int = 3, model: str = "bge-m3") -> list[dict]:
    return [
        {"chunkId": f"c-{index}", "vector": [1.0, float(index), 0.0, 0.0],
         "documentId": f"doc-{index}", "model": model, "text": f"chunk-{index}"}
        for index in range(count)
    ]


class FailingStore(InMemoryVectorIndexStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next = False

    def create_collection(self, name, records, **kwargs):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("milvus unavailable")
        return super().create_collection(name, records, **kwargs)


def test_spec_defaults_and_reserved_contract() -> None:
    spec = IndexSpec(kb_id="kb-1")
    assert spec.index_type == "HNSW"
    assert spec.metric == "COSINE"
    assert spec.index_slot == "auto"
    assert dict(spec.params or {}) == DEFAULT_PARAMS
    payload = spec.to_dict()
    assert payload["alias"].endswith("__hnsw")
    assert set(payload) >= {"indexType", "metric", "params", "indexSlot", "alias", "model", "version", "env"}
    assert "kb-1" in spec.alias_name()
    assert spec.collection_name("online", 1).endswith("__online__v1")
    with pytest.raises(ValueError):
        IndexSpec(kb_id="kb-1", index_type="IVF_FLAT")
    with pytest.raises(ValueError):
        IndexSpec(kb_id="kb-1", metric="HAMMING")
    with pytest.raises(ValueError):
        IndexSpec(kb_id="kb-1", index_slot="slot-x")


def test_alias_reserves_multi_model_and_index_type() -> None:
    base = IndexSpec(kb_id="kb-1", model="bge-m3")
    other = IndexSpec(kb_id="kb-1", model="text-embedding-v3")
    assert base.alias_name() != other.alias_name()
    assert "__hnsw" in base.alias_name()
    assert IndexSpec(kb_id="kb-1", alias="custom_alias").alias_name() == "custom_alias"


def test_build_takes_effect_immediately_and_is_searchable() -> None:
    store = InMemoryVectorIndexStore()
    service = HnswIndexService(store, resource_sampler=lambda: {"cpuPercent": 30.0, "gpuUtilization": 4.0})
    build = service.build("kb-1", records(), dimension=4)
    assert build.status == READY
    assert build.progress == 100 and build.indexed_chunks == 3
    assert build.metrics["durationMs"] >= 0
    assert build.metrics["resources"]["cpuPercent"] == 30.0
    assert store.get_alias(build.alias) == build.collection

    hits = service.search("kb-1", [1.0, 2.0, 0.0, 0.0], top_k=2)
    assert [hit["chunkId"] for hit in hits] == ["c-2", "c-1"]
    status = service.status("kb-1", doc_count=3)
    assert status["indexStatus"] == READY
    assert status["collection"] == build.collection
    assert status["chunkCount"] == 3 and status["docCount"] == 3
    assert status["lastBuildId"] == build.build_id

    empty = service.status("kb-missing")
    assert empty["indexStatus"] == EMPTY and empty["collection"] == ""


def test_build_failure_keeps_old_index_then_retry_switches() -> None:
    store = FailingStore()
    service = HnswIndexService(store)
    first = service.build("kb-1", records(3), dimension=4)
    assert first.status == READY

    store.fail_next = True
    second = service.build("kb-1", records(5), dimension=4)
    assert second.status == FAILED
    assert second.error and "milvus unavailable" in second.error
    assert store.get_alias(first.alias) == first.collection, "构建失败必须继续使用旧索引"
    assert len(service.search("kb-1", [1.0, 1.0, 0.0, 0.0], top_k=9)) == 3

    retried = service.retry(second.build_id, dimension=4)
    assert retried.status == READY
    assert retried.attempt == 2
    assert retried.collection != second.collection
    assert store.get_alias(retried.alias) == retried.collection
    assert len(service.search("kb-1", [1.0, 1.0, 0.0, 0.0], top_k=9)) == 5
    with pytest.raises(ValueError):
        service.retry(retried.build_id)


def test_rollback_restores_previous_collection() -> None:
    store = InMemoryVectorIndexStore()
    service = HnswIndexService(store)
    old = service.build("kb-1", records(2), dimension=4)
    new = service.full_rebuild("kb-1", records(5), dimension=4)
    assert new.previous_collection == old.collection
    assert store.get_alias(new.alias) == new.collection

    rolled = service.rollback(new.build_id)
    assert rolled.rolled_back is True
    assert store.get_alias(new.alias) == old.collection
    assert len(service.search("kb-1", [1.0, 0.0, 0.0, 0.0], top_k=9)) == 2
    with pytest.raises(ValueError):
        service.rollback(old.build_id)


def test_full_rebuild_versions_collection_and_tracks_builds() -> None:
    service = HnswIndexService()
    first = service.full_rebuild("kb-1", records(2), dimension=4)
    second = service.full_rebuild("kb-1", records(4), dimension=4)
    assert first.collection != second.collection
    assert second.collection.endswith("__v2")
    assert second.previous_collection == first.collection
    builds = service.list_builds("kb-1")
    assert [build.build_id for build in builds] == [first.build_id, second.build_id]
    assert service.status("kb-1")["lastBuildId"] == second.build_id


def test_dimension_mismatch_fails_build_without_switching() -> None:
    service = HnswIndexService()
    good = service.build("kb-1", records(2), dimension=4)
    bad = service.build("kb-1", [{"chunkId": "x", "vector": [1.0, 0.0]}], dimension=4)
    assert bad.status == FAILED
    assert "维度" in (bad.error or "")
    assert service.store.get_alias(good.alias) == good.collection
