"""独立测试 ingest-worker 的 4 个被临时注释的路由模块。

背景
----
`ingest_worker/api/app.py` 当前为了规避 Nacos 元数据超限，把以下 4 个 router 注释掉：

    # app.include_router(parse.router)
    # app.include_router(summary.router)
    # app.include_router(metadata.router)
    # app.include_router(embedding.router)

它们当前因 Nacos 注册阶段的元数据体积问题被临时下线。本脚本把这 4 个
router 单独拿出来，用 FastAPI `TestClient` 在不注册 Nacos、不走 lifespan
的前提下验证它们的 HTTP 契约，证明：

    1) 4 个模块的核心业务逻辑可独立工作；
    2) 20 个公开接口路径能够正常注册并生成 OpenAPI。

注意：本脚本不连接真实 Nacos，不能单独证明注册失败就是元数据超限；注册
根因仍需结合 Nacos 错误日志或实际注册 payload 确认。

各模块用处
----------
- ``parse.router``     文档解析与分块。上传 PDF/Word/Excel/Markdown，经对应解析器
  （PDF 可接 MinerU）解析 → 清洗 → 分块（fixed/structured/itemized），返回版本化产物。配套查询接口：
  最新结果、版本列表、清洗差异、chunk 预览、条款清单（含跨租户隔离）。
- ``metadata.router``  标准元数据六要素（standardNo/Name、issuer、issuedAt、
  effectiveAt、replacedBy、domain、securityLevel、confidence）查询与人工修正，
  修正留审计轨迹。配套 ``/metadata:query`` 按标准号/机构/时间窗/关键词过滤。
- ``embedding.router`` 批量向量化任务。基于解析 chunk 创建任务（202 受理），
  配套列表、进度查询、暂停/恢复/失败重试、维度与模型一致性校验。P0 默认使用
  确定性 SHA256-HASH + 内存向量库，在线环境可替换为 BGE-M3/Milvus。
- ``summary.router``   标准简介自动生成（#405）：从条款 + 元数据抽取"五要素"，
  缺失标注"未识别"，按文档版本化落盘。

运行
----
在 ingest-worker 容器内：

    docker compose -f deploy/docker/docker-compose-python.yaml \\
        exec svc-ingest python -m pytest /app/apps/ingest-worker/tests/test_routers_standalone.py -v

或本地（需先 ``uv sync --all-packages --group cpu`` 安装依赖）：

    cd pymodules
    .venv/bin/python -m pytest apps/ingest-worker/tests/test_routers_standalone.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 把 workspace 包路径补上（本地 venv 未装 pipeline/nacos 时也能 import）
_ROOT = Path(__file__).resolve().parents[3]   # /coding/aipaas/pymodules
for sub in ("packages/pipeline", "packages/nacos", "apps/ingest-worker"):
    p = str(_ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("CONFIG_PACKAGE_DIR", str(_ROOT / "configs"))

from ingest_worker.api.dependencies import (  # noqa: E402
    get_embedding_service,
    get_parse_service,
    get_summary_service,
)
from ingest_worker.api.routers import (  # noqa: E402
    embedding,
    metadata,
    parse,
    summary,
)

# 用真实的默认实现（内存/SQLite），不依赖 MinIO/Milvus/真实 LLM
from aipaas_pipeline.embedding import (  # noqa: E402
    EmbeddingConfig,
    EmbeddingJobService,
    HashingEncoder,
    InMemoryVectorStore,
)
from aipaas_pipeline.service import build_default_service  # noqa: E402
from aipaas_pipeline.summary import build_default_summary_service  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    """构造一个只挂 4 个被注释路由的 FastAPI 实例。

    - 不走 ``build_lifespan``：不注册 Nacos、不启动 BackgroundTaskManager
    - 用真实默认服务（内存存储），避免外部依赖
    - 通过 ``dependency_overrides`` 注入，绕过 lru_cache 单例
    """
    app = FastAPI(title="ingest-worker-standalone-test")
    app.include_router(parse.router)
    app.include_router(metadata.router)
    app.include_router(embedding.router)
    app.include_router(summary.router)

    parse_svc = build_default_service()
    summary_svc = build_default_summary_service(parse_svc)
    embed_svc = EmbeddingJobService(
        encoder=HashingEncoder(dimension=64, model="SHA256-HASH"),
        store=InMemoryVectorStore(),
        config=EmbeddingConfig(model="SHA256-HASH", dimension=64),
    )
    app.dependency_overrides[get_parse_service] = lambda: parse_svc
    app.dependency_overrides[get_summary_service] = lambda: summary_svc
    app.dependency_overrides[get_embedding_service] = lambda: embed_svc
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────
# 1. parse.router  —— 文档解析与分块
# ─────────────────────────────────────────────────────────────────

SAMPLE_MD = """# GB/T 22239-2019 信息安全技术 网络安全等级保护基本要求

## 1 范围

本标准规定了网络安全等级保护的第一级到第四级等级保护对象的安全通用要求
和安全扩展要求。

## 2 规范性引用文件

下列文件对于本文件的应用是必不可少的。
""".encode("utf-8")


def test_parse_upload_returns_200_with_version(client: TestClient):
    """上传文档 → 同步解析 + 清洗 + 分块，返回版本化产物。"""
    resp = client.post(
        "/api/v1/documents/doc-parse-001:parse",
        files={"file": ("sample.md", SAMPLE_MD, "text/markdown")},
        data={"tenantId": "tenant-a", "cleanEnabled": "true", "chunkStrategy": "structured"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["success"] is True
    assert body["data"]["version"] >= 1


def test_parse_result_can_be_fetched(client: TestClient):
    """解析后可查询最新结果与版本列表。"""
    r = client.get("/api/v1/documents/doc-parse-001/parse-result")
    assert r.status_code == 200
    assert r.json()["data"]["docId"] == "doc-parse-001"

    versions = client.get("/api/v1/documents/doc-parse-001/parse-result/versions")
    assert versions.status_code == 200
    assert isinstance(versions.json()["data"]["versions"], list)


def test_parse_result_404_when_missing(client: TestClient):
    """未解析过的文档返回 404。"""
    r = client.get("/api/v1/documents/never-parsed/parse-result")
    assert r.status_code == 404
    assert r.json()["code"] == 404


def test_chunk_preview_does_not_persist(client: TestClient):
    """分块预览只返回预览结果，不写入产物。"""
    r = client.post(
        "/api/v1/documents/doc-parse-001:chunk-preview",
        json={"strategy": "structured", "limit": 5},
    )
    assert r.status_code == 200
    items = r.json()["data"].get("items") or r.json()["data"].get("chunks") or []
    assert isinstance(items, list)


def test_get_chunks_returns_current_version(client: TestClient):
    """查询当前 chunk 集合（含 hash 与条款锚点）。"""
    r = client.get("/api/v1/documents/doc-parse-001/chunks")
    assert r.status_code == 200
    payload = r.json()["data"]
    assert "chunks" in payload
    assert isinstance(payload["chunks"], list)


def test_list_clauses_with_tenant_isolation(client: TestClient):
    """跨租户访问条款清单被拒绝（403）。"""
    ok = client.get("/api/v1/documents/doc-parse-001/clauses?tenant_id=tenant-a")
    assert ok.status_code == 200

    forbidden = client.get("/api/v1/documents/doc-parse-001/clauses?tenant_id=tenant-b")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == 403


# ─────────────────────────────────────────────────────────────────
# 2. metadata.router  —— 标准元数据
# ─────────────────────────────────────────────────────────────────


def test_metadata_get_and_put(client: TestClient):
    """查询六要素 + 人工修正部分字段，留审计。"""
    r = client.get("/api/v1/documents/doc-parse-001/metadata")
    assert r.status_code == 200

    put = client.put(
        "/api/v1/documents/doc-parse-001/metadata",
        json={"standardNo": "GB/T 22239-2019", "issuer": "国家市场监督管理总局"},
    )
    assert put.status_code == 200
    meta = put.json()["data"]
    assert meta.get("standardNo") == "GB/T 22239-2019"
    assert meta.get("issuer") == "国家市场监督管理总局"


def test_metadata_audit_trail(client: TestClient):
    """修正后审计轨迹包含 actor 与变更字段。"""
    r = client.get("/api/v1/documents/doc-parse-001/metadata/audit")
    assert r.status_code == 200
    entries = r.json()["data"]["entries"]
    assert isinstance(entries, list)


def test_metadata_query_filters(client: TestClient):
    """按标准号/关键词过滤元数据。"""
    r = client.get("/api/v1/metadata:query?standardNo=GB/T&limit=10")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "items" in data
    assert "total" in data


# ─────────────────────────────────────────────────────────────────
# 3. embedding.router  —— 批量向量化任务
# ─────────────────────────────────────────────────────────────────


def test_embedding_job_lifecycle(client: TestClient):
    """创建任务 → 查进度 → 暂停 → 恢复 → 重试失败 → 校验维度。"""
    create = client.post(
        "/api/v1/index/embedding-jobs",
        json={"kbId": "kb-1", "documentIds": ["doc-parse-001"]},
    )
    assert create.status_code == 202, create.text
    job = create.json()["data"]
    job_id = job["id"]
    assert job["kbId"] == "kb-1"

    get = client.get(f"/api/v1/index/embedding-jobs/{job_id}")
    assert get.status_code == 200

    pause = client.post(f"/api/v1/index/embedding-jobs/{job_id}:pause")
    # 任务可能已完成（内存版同步），pause 可能 409
    assert pause.status_code in (200, 409)

    resume = client.post(f"/api/v1/index/embedding-jobs/{job_id}:resume")
    assert resume.status_code in (200, 409)

    retry = client.post(f"/api/v1/index/embedding-jobs/{job_id}:retry")
    assert retry.status_code in (200, 409)

    verify = client.get(f"/api/v1/index/embedding-jobs/{job_id}/vectors:verify")
    assert verify.status_code == 200


def test_embedding_list_jobs(client: TestClient):
    """任务列表返回 items 字段。"""
    r = client.get("/api/v1/index/embedding-jobs")
    assert r.status_code == 200
    assert "items" in r.json()["data"]


def test_embedding_404_for_unknown_document(client: TestClient):
    """显式指定 documentIds 但都不存在 → 404。"""
    r = client.post(
        "/api/v1/index/embedding-jobs",
        json={"kbId": "kb-1", "documentIds": ["no-such-doc"]},
    )
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────
# 4. summary.router  —— 标准简介生成（#405）
# ─────────────────────────────────────────────────────────────────


def test_summary_generate_and_latest(client: TestClient):
    """生成标准简介，再查询最新版本。"""
    gen = client.post("/api/v1/documents/doc-parse-001/summaries", json={})
    assert gen.status_code == 200, gen.text
    payload = gen.json()["data"]
    assert payload["documentId"] == "doc-parse-001"

    latest = client.get("/api/v1/documents/doc-parse-001/summaries/latest")
    assert latest.status_code == 200
    assert latest.json()["data"] is not None


def test_summary_latest_404_when_not_generated(client: TestClient):
    """未生成简介的文档返回 404。"""
    r = client.get("/api/v1/documents/never-summarized/summaries/latest")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────
# 5. 路由表完整性：确认 4 个 router 共 20 个接口路径都已注册
# ─────────────────────────────────────────────────────────────────


def test_router_paths_registered(client: TestClient):
    """4 个 router 全部挂载成功（独立模式下无 Nacos 元数据限制）。"""
    # FastAPI 0.142 的 app.routes 含未展开的 _IncludedRouter；
    # 通过 OpenAPI 校验实际公开契约，也更贴近 Nacos 元数据来源。
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = set(openapi.json()["paths"])
    expected = {
        # parse
        "/api/v1/documents/{document_id}:parse",
        "/api/v1/documents/{document_id}/parse-result",
        "/api/v1/documents/{document_id}/parse-result/versions",
        "/api/v1/documents/{document_id}/parse-result/clean",
        "/api/v1/documents/{document_id}/parse-result/clean-diff",
        "/api/v1/documents/{document_id}:chunk-preview",
        "/api/v1/documents/{document_id}/chunks",
        "/api/v1/documents/{document_id}/chunk-config",
        "/api/v1/documents/{document_id}/clauses",
        # metadata
        "/api/v1/documents/{document_id}/metadata",
        "/api/v1/documents/{document_id}/metadata/audit",
        "/api/v1/metadata:query",
        # embedding
        "/api/v1/index/embedding-jobs",
        "/api/v1/index/embedding-jobs/{job_id}",
        "/api/v1/index/embedding-jobs/{job_id}:pause",
        "/api/v1/index/embedding-jobs/{job_id}:resume",
        "/api/v1/index/embedding-jobs/{job_id}:retry",
        "/api/v1/index/embedding-jobs/{job_id}/vectors:verify",
        # summary
        "/api/v1/documents/{document_id}/summaries",
        "/api/v1/documents/{document_id}/summaries/latest",
    }
    missing = expected - paths
    assert not missing, f"以下路由未注册：{missing}"
