"""AIPaaS P0 Python AI 服务骨架（FastAPI）。

仅提供：
- /healthz、/readyz 探针（200）
- /api/v1/system/info 契约元信息
- 其余 /api/v1 契约路由统一返回 501 + SYS.NOT_IMPLEMENTED（不实现业务逻辑）

真实 AI 能力（解析/分块/向量化/检索/生成）由 P0 后续迭代实现；本服务与
`aipaas-contracts/openapi/aipaas-openapi-v1.yaml` 保持同一路径与信封。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

CONTRACT_VERSION = os.getenv("AIPAAS_CONTRACT_VERSION", "1.1.0-p0")
API_VERSION = os.getenv("AIPAAS_API_VERSION", "v1")
ENV = os.getenv("AIPAAS_ENV", "dev")
SERVICE = "rag-ai-api"

app = FastAPI(
    title="AIPaaS P0 Python AI Service (Skeleton)",
    version="0.1.0",
    description="契约骨架；业务实现见 P0 后续迭代。契约以 aipaas-contracts 为准。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)


def _trace_id(request: Request) -> str:
    return request.headers.get("X-Request-Id") or "tr_" + uuid.uuid4().hex


def _envelope(code: str, message: str, data, trace_id: str) -> dict:
    return {"code": code, "message": message, "data": data, "traceId": trace_id}


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = _trace_id(request)
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.get("/healthz")
async def healthz():
    return {"status": "UP"}


# Prometheus 指标端点（进程/GC 等基础指标；业务指标在 P0 迭代中补充）
app.mount("/metrics", make_asgi_app())


@app.get("/readyz")
async def readyz():
    return {"status": "UP", "checks": {"config": {"status": "UP"}, "model": {"status": "UP"}}}


@app.get("/api/v1/system/info")
async def system_info(request: Request):
    return _envelope("OK", "success", {
        "service": SERVICE,
        "version": "0.1.0",
        "apiVersion": API_VERSION,
        "contractVersion": CONTRACT_VERSION,
        "contractStatus": "frozen",
        "env": ENV,
        "p0FeatureCount": 44,
        "domains": ["知识构建与索引", "检索引擎", "生成与推理", "模型服务"],
        "capabilities": ["RAG"],
        "startedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, request.state.trace_id)


@app.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def contract_fallback(path: str, request: Request):
    """契约已定义、骨架未实现：保持统一信封，便于前后端提前联调错误分支。"""
    return JSONResponse(
        status_code=501,
        content=_envelope(
            "SYS.NOT_IMPLEMENTED",
            "P0 契约已定义，Python AI 骨架尚未实现该接口",
            {"retryable": False, "path": path, "owner": "python"},
            request.state.trace_id,
        ),
    )
