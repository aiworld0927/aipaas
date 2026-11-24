"""仅用于本地 curl 演示的 app-ingest-worker 路由服务。

不挂载 Nacos lifespan，不连接 MinIO/Milvus/真实 LLM，也不修改正式 app.py。
默认监听 127.0.0.1:18081，解析产物写入 /tmp/aipaas-router-curl-data。

启动（在 pymodules 目录）：
    .venv/bin/python apps/app-ingest-worker/tests/standalone_router_server.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for sub in ("packages/pipeline", "packages/nacos", "apps/app-ingest-worker"):
    path = str(_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("CONFIG_PACKAGE_DIR", str(_ROOT / "configs"))
os.environ.setdefault("AIPAAS_PARSE_DATA_DIR", "/tmp/aipaas-router-curl-data")
os.environ.setdefault("AIPAAS_PARSE_ARTIFACT_BACKEND", "memory")

from fastapi import FastAPI  # noqa: E402

from aipaas_pipeline.embedding import (  # noqa: E402
    EmbeddingConfig,
    EmbeddingJobService,
    HashingEncoder,
    InMemoryVectorStore,
)
from aipaas_pipeline.service import build_default_service  # noqa: E402
from aipaas_pipeline.summary import build_default_summary_service  # noqa: E402
from app_ingest_worker.api.dependencies import (  # noqa: E402
    get_embedding_service,
    get_parse_service,
    get_summary_service,
)
from app_ingest_worker.api.routers import embedding, metadata, parse, summary  # noqa: E402


def create_app() -> FastAPI:
    """只装配 curl 演示所需的四个 router 和内存/本地实现。"""
    app = FastAPI(title="app-ingest-worker-router-curl-demo")
    app.include_router(parse.router)
    app.include_router(metadata.router)
    app.include_router(embedding.router)
    app.include_router(summary.router)

    parse_service = build_default_service()
    summary_service = build_default_summary_service(parse_service)
    embedding_service = EmbeddingJobService(
        encoder=HashingEncoder(dimension=64, model="SHA256-HASH"),
        store=InMemoryVectorStore(),
        config=EmbeddingConfig(model="SHA256-HASH", dimension=64),
    )
    app.dependency_overrides[get_parse_service] = lambda: parse_service
    app.dependency_overrides[get_summary_service] = lambda: summary_service
    app.dependency_overrides[get_embedding_service] = lambda: embedding_service
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "18081")),
        access_log=False,
    )
