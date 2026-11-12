# ===========================================================================
# AIPaaS 查询服务 Dockerfile（独立部署单元，端口 8002）
# ---------------------------------------------------------------------------
# 构建：
#   cd pymodules
#   docker build -f apps/query-worker/Dockerfile -t svc-query .
# ===========================================================================

# ===========================================================================
# AIPaaS 查询服务 Dockerfile（独立部署单元，端口 8002）
# ---------------------------------------------------------------------------
# 构建：
#   cd pymodules
#   docker build -f apps/query-worker/Dockerfile -t aipaas/svc-query:latest .
#
# 运行（配置包外部映射：部署时把宿主机配置目录挂到 /app/configs）：
#   docker run -d --name svc-query -p 8002:8002 \
#     -v /host/path/logs:/app/logs \
#     -v /host/path/aipaas-configs:/app/configs:ro \
#     -e APP_ENV=devcompany -e CONFIG_DIR=/app/configs \
#     aipaas/svc-query:latest
#
# docker-compose 中自动挂载日志卷与配置包，日志文件命名为 query-worker_YYYYMMDD.log
# ===========================================================================

# ---- 阶段 1：依赖构建 ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELSCOPE_CACHE=/app/models/modelscope_cache \
    HF_HOME=/app/models/huggingface \
    UV_HTTP_TIMEOUT=120

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/query-worker ./apps/query-worker

RUN uv sync --package svc-query --no-dev --frozen 2>&1

# ---- 阶段 2：运行时 ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELSCOPE_CACHE=/app/models/modelscope_cache \
    HF_HOME=/app/models/huggingface \
    PROJECT_ROOT=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN touch .env && mkdir -p /app/logs /app/models

COPY --from=builder /app/.venv .venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY packages ./packages
COPY apps/query-worker ./apps/query-worker
COPY configs ./configs
COPY pyproject.toml uv.lock ./

# 数据卷声明：日志目录（由 compose 或 -v 挂载到宿主机）
VOLUME ["/app/logs", "/app/models"]

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:${QUERY_WORKER_PORT:-8002}/health')" || exit 1

CMD [".venv/bin/python", "-m", "query_worker.api.service"]
