"""#44 批量向量化：成功率、维度校验、暂停恢复、失败重试、幂等 upsert、资源监控。"""
from __future__ import annotations

import pytest

from aipaas_pipeline.chunking import Chunk
from aipaas_pipeline.embedding import (
    FAILED,
    PAUSED,
    SUCCEEDED,
    EmbeddingConfig,
    EmbeddingJobService,
    HashingEncoder,
    InMemoryVectorStore,
)


def make_chunks(count: int, doc_id: str = "doc-1") -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"c-{index}",
            document_id=doc_id,
            ordinal=index,
            chunk_type="clause",
            content=f"第{index}条规定金融数据安全要求。",
            clause_no=f"{index // 10 + 1}.{index % 10 + 1}",
        )
        for index in range(count)
    ]


class FlakyEncoder(HashingEncoder):
    """第一批失败一次，用于验证单批失败隔离。"""

    def __init__(self, dimension: int = 64) -> None:
        super().__init__(dimension)
        self.fail_once = True

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("batch transient failure")
        return super().encode(texts)


class WrongDimensionEncoder(HashingEncoder):
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 8 for _ in texts]


def test_batch_vectorization_meets_success_rate_and_metrics() -> None:
    store = InMemoryVectorStore()
    service = EmbeddingJobService(
        HashingEncoder(64), store, EmbeddingConfig(dimension=64, batch_size=25),
        resource_sampler=lambda: {"cpuPercent": 12.5, "gpuUtilization": None, "source": "test"},
    )
    job = service.create_job("kb-main", make_chunks(250))
    result = service.run(job.job_id)
    assert result.status == SUCCEEDED
    assert result.total == 250 and result.processed == 250 and result.failed == 0
    assert result.success_rate == 100.0
    assert result.progress == 100
    assert result.metrics["batchCount"] == 10
    assert result.metrics["durationMs"] >= 0
    assert result.metrics["resources"]["cpuPercent"] == 12.5
    assert result.metrics["resources"]["samples"] == 10
    verify = service.verify_vectors(job.job_id)
    assert verify["dimensionValid"] is True
    assert verify["stored"] == 250
    assert store.count(verify["collection"]) == 250


def test_failed_batch_is_isolated_located_and_retryable() -> None:
    encoder = FlakyEncoder(64)
    store = InMemoryVectorStore()
    service = EmbeddingJobService(encoder, store, EmbeddingConfig(dimension=64, batch_size=10))
    job = service.create_job("kb-retry", make_chunks(1000))
    first = service.run(job.job_id)
    assert first.status == SUCCEEDED, "单批失败不得中断整体任务"
    assert first.failed == 10 and first.processed == 990
    assert first.success_rate >= 99.0
    assert len(first.failures) == 10
    failure = first.failures[0]
    assert failure["chunkId"].startswith("c-")
    assert failure["code"] == "EMBED.BATCH_FAILED"
    assert failure["retryable"] is True and failure["documentId"] == "doc-1"

    second = service.retry_failures(job.job_id)
    assert second.status == SUCCEEDED
    assert second.failed == 0 and second.processed == 1000
    assert second.success_rate == 100.0
    assert second.failures == []
    assert service.verify_vectors(job.job_id)["stored"] == 1000


def test_dimension_mismatch_is_rejected_without_writing() -> None:
    store = InMemoryVectorStore()
    service = EmbeddingJobService(
        WrongDimensionEncoder(64), store, EmbeddingConfig(dimension=64, batch_size=8)
    )
    job = service.create_job("kb-dim", make_chunks(20))
    result = service.run(job.job_id)
    assert result.status == FAILED
    assert result.failed == 20 and result.processed == 0
    assert {item["code"] for item in result.failures} == {"EMBED.DIM_MISMATCH"}
    assert all(item["retryable"] is False for item in result.failures)
    assert service.verify_vectors(job.job_id)["stored"] == 0


def test_encoder_and_collection_dimension_must_match() -> None:
    with pytest.raises(ValueError, match="编码器维度"):
        EmbeddingJobService(HashingEncoder(32), InMemoryVectorStore(), EmbeddingConfig(dimension=64))

    store = InMemoryVectorStore()
    service = EmbeddingJobService(HashingEncoder(64), store, EmbeddingConfig(dimension=64))
    service.run(service.create_job("kb-d", make_chunks(2)).job_id)
    with pytest.raises(ValueError, match="集合维度"):
        EmbeddingJobService(HashingEncoder(32), store, EmbeddingConfig(dimension=32)).create_job(
            "kb-d", make_chunks(2)
        )


def test_pause_and_resume_preserve_progress() -> None:
    service = EmbeddingJobService(HashingEncoder(64), InMemoryVectorStore(), EmbeddingConfig(batch_size=10))
    job = service.create_job("kb-pause", make_chunks(100))
    paused = service.run(job.job_id, max_batches=3)
    assert paused.status == PAUSED
    assert paused.processed == 30 and paused.progress == 30
    resumed = service.resume(job.job_id)
    assert resumed.status == SUCCEEDED
    assert resumed.processed == 100 and resumed.progress == 100
    assert resumed.metrics["batchCount"] == 10


def test_upsert_is_idempotent_and_idempotency_key_dedupes_jobs() -> None:
    store = InMemoryVectorStore()
    service = EmbeddingJobService(HashingEncoder(64), store, EmbeddingConfig(batch_size=10))
    chunks = make_chunks(30)
    job = service.create_job("kb-idem", chunks, idempotency_key="same-key")
    same = service.create_job("kb-idem", chunks, idempotency_key="same-key")
    assert same.job_id == job.job_id

    service.run(job.job_id)
    service.run(job.job_id)  # 已完成任务再次执行不得重复计数或重复写入
    collection = service.verify_vectors(job.job_id)["collection"]
    assert store.count(collection) == 30
    assert len(store.all(collection)) == 30


# --------------------------------------------------------------------------- HTTP API


def _client(parse_service, embedding_service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ingest_worker.api.dependencies import get_embedding_service, get_parse_service
    from ingest_worker.api.routers import embedding, parse

    app = FastAPI()
    app.include_router(parse.router)
    app.include_router(embedding.router)
    app.dependency_overrides[get_parse_service] = lambda: parse_service
    app.dependency_overrides[get_embedding_service] = lambda: embedding_service
    return TestClient(app)


def test_embedding_job_api_create_progress_pause_resume_and_retry(service) -> None:
    from aipaas_pipeline.samples import md_jrt_code

    embeddings = EmbeddingJobService(HashingEncoder(16), InMemoryVectorStore(), EmbeddingConfig(dimension=16))
    client = _client(service, embeddings)
    uploaded = client.post(
        "/api/v1/documents/api-emb:parse",
        files={"file": ("jrt.md", md_jrt_code(), "text/markdown")},
    )
    assert uploaded.status_code == 200

    created = client.post(
        "/api/v1/index/embedding-jobs",
        json={"kbId": "kb-api", "documentIds": ["api-emb"]},
        headers={"Idempotency-Key": "idem-api"},
    )
    assert created.status_code == 202, created.text
    job = created.json()["data"]
    assert job["total"] > 0

    again = client.post(
        "/api/v1/index/embedding-jobs",
        json={"kbId": "kb-api", "documentIds": ["api-emb"]},
        headers={"Idempotency-Key": "idem-api"},
    )
    assert again.json()["data"]["id"] == job["id"], "同一幂等键不得重复创建任务"

    status = client.get(f"/api/v1/index/embedding-jobs/{job['id']}").json()["data"]
    assert status["status"] == "SUCCEEDED"
    assert status["progress"] == 100 and status["successRate"] == 100.0
    assert status["metrics"]["resources"]

    verify = client.get(f"/api/v1/index/embedding-jobs/{job['id']}/vectors:verify").json()["data"]
    assert verify["dimensionValid"] is True and verify["stored"] == job["total"]

    retried = client.post(f"/api/v1/index/embedding-jobs/{job['id']}:retry")
    assert retried.status_code == 200
    assert retried.json()["data"]["failed"] == 0

    missing = client.get("/api/v1/index/embedding-jobs/nope")
    assert missing.status_code == 404
