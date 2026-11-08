# ===========================================================================
# AIPaaS 导入服务 Dockerfile（独立部署单元，端口 8000）
# ---------------------------------------------------------------------------
# 构建：
#   cd pymodules
#   docker build -f apps/ingest-worker/Dockerfile -t svc-ingest .
# ===========================================================================

# ---- 阶段 1：依赖构建 ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELSCOPE_CACHE=/app/models/modelscope_cache \
    HF_HOME=/app/models/huggingface

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# workspace 配置 + 全部成员源码（editable install 需要完整源码树）
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/ingest-worker ./apps/ingest-worker

RUN uv sync --package svc-ingest --no-dev 2>&1

# ---- 阶段 2：运行时 ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELSCOPE_CACHE=/app/models/modelscope_cache \
    HF_HOME=/app/models/huggingface \
    PROJECT_ROOT=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN touch .env

COPY --from=builder /app/.venv .venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY packages ./packages
COPY apps/ingest-worker ./apps/ingest-worker
COPY configs ./configs
COPY pyproject.toml uv.lock ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD [".venv/bin/python", "-m", "uvicorn", \
     "ingest_worker.api.service:app", \
     "--host", "0.0.0.0", "--port", "8000"]
