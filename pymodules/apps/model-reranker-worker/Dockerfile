# ===========================================================================
# AIPaaS 重排序模型服务 Dockerfile（bge-reranker-large，独立部署单元，端口 8102）
# ---------------------------------------------------------------------------
# 构建：
#   cd pymodules
#   docker build -f apps/model-reranker-worker/Dockerfile -t aipaas/svc-model-reranker:latest .
#
# 运行（模型目录挂载到容器内 /app/models/bge-reranker-large）：
#   docker run -d --name svc-model-reranker -p 8102:8102 --gpus all \
#     -v /host/models/bge-reranker-large:/app/models/bge-reranker-large:ro \
#     -v /host/path/logs:/app/logs \
#     -e BGE_RERANKER_LARGE=/app/models/bge-reranker-large \
#     -e BGE_RERANKER_DEVICE=cuda \
#     -e BGE_RERANKER_FP16=1 \
#     aipaas/svc-model-reranker:latest
# ===========================================================================

# ---- 阶段 1：依赖构建 ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=120

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# workspace 配置 + 依赖的成员源码（editable install 需要源码树）
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/model-reranker-worker ./apps/model-reranker-worker

# torch / flagembedding 在根 cpu/gpu 分组中，sync 必须带分组；
# 这里先用 cpu 分组完成全量依赖解析，下一步再把 torch 替换为 CUDA 构建
RUN uv sync --package model-reranker-worker --no-dev --group cpu 2>&1

# 推理服务面向 GPU 部署：把 CPU 版 torch 替换为 CUDA 12.8 构建
RUN uv pip install --python /app/.venv/bin/python --reinstall \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    torch==2.11.0+cu128

# ---- 阶段 2：运行时 ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_ROOT=/app \
    LOG_DIR=/app/logs \
    LOG_FILE_PREFIX=model-reranker-worker \
    LOG_FILE_ENABLE=True \
    LOG_FILE_LEVEL=INFO \
    LOG_FILE_RETENTION="7 days" \
    LOG_CONSOLE_ENABLE=True \
    LOG_CONSOLE_LEVEL=INFO \
    MODEL_RERANKER_HOST=0.0.0.0 \
    MODEL_RERANKER_PORT=8102 \
    BGE_RERANKER_LARGE=/app/models/bge-reranker-large \
    BGE_RERANKER_FP16=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN touch .env && mkdir -p /app/logs /app/models

COPY --from=builder /app/.venv .venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY packages ./packages
COPY apps/model-reranker-worker ./apps/model-reranker-worker
COPY pyproject.toml uv.lock ./

# 数据卷声明：日志目录 + 模型目录（由 compose 或 -v 挂载到宿主机）
VOLUME ["/app/logs", "/app/models"]

EXPOSE 8102

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8102/health')" || exit 1

CMD [".venv/bin/python", "-m", "uvicorn", \
     "model_reranker_worker.api.service:app", \
     "--host", "0.0.0.0", "--port", "8102"]
