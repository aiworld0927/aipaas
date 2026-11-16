# ===========================================================================
# AIPaaS 导入服务 Dockerfile（独立部署单元，端口 8001）
# ---------------------------------------------------------------------------
# 构建：
#   cd pymodules
#   docker build -f apps/ingest-worker/Dockerfile -t svc-ingest .
# ===========================================================================

# ===========================================================================
# AIPaaS 导入服务 Dockerfile（独立部署单元，端口 8001）
# ---------------------------------------------------------------------------
# 构建：
#   cd pymodules
#   docker build -f apps/ingest-worker/Dockerfile -t aipaas/svc-ingest:latest .
#
# 运行（配置包外部映射：部署时把宿主机配置目录挂到 /app/configs）：
#   docker run -d --name svc-ingest -p 8001:8001 \
#     -v /host/path/logs:/app/logs \
#     -v /host/path/aipaas-configs:/app/configs:ro \
#     -e APP_ENV=devcompany -e CONFIG_DIR=/app/configs \
#     aipaas/svc-ingest:latest
#
# docker-compose 中自动挂载日志卷与配置包，日志文件命名为 ingest-worker_YYYYMMDD.log
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

# ------------------------------------------------------------
# 核心：把「装第三方依赖」和「editable install 本地包」拆成两层
#       改源码时只让第二层失效，第一层（下载依赖）缓存命中
# ------------------------------------------------------------

# 第 1 层：只 COPY 锁文件 → uv sync 只装第三方依赖
# --no-install-workspace  跳过 workspace 成员（editable install 在第 2 层做）
# --all-packages          读取所有 workspace 成员的 pyproject.toml 装齐传递依赖
# --frozen                严格按 uv.lock，不重新解析
# --no-dev                不装开发依赖
# 这层只在 pyproject.toml / uv.lock 变更时失效（改源码不会让它失效）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-workspace --all-packages \
 && uv pip install --python /app/.venv/bin/python hatchling editables

# 第 2 层：COPY 本地包源码 → editable install
# 改 packages/ 或 apps/ 里的源码只让这层失效，第 1 层缓存命中
COPY packages ./packages
COPY apps/ingest-worker ./apps/ingest-worker
RUN uv pip install --python /app/.venv/bin/python --no-deps --no-build-isolation \
      -e packages/common \
      -e packages/model-clients \
      -e packages/nacos \
      -e packages/retrieval \
      -e packages/storage \
      -e apps/ingest-worker

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
COPY apps/ingest-worker ./apps/ingest-worker
COPY configs ./configs
COPY pyproject.toml uv.lock ./

# 数据卷声明：日志目录（由 compose 或 -v 挂载到宿主机）
VOLUME ["/app/logs", "/app/models"]

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:${INGEST_WORKER_PORT:-8001}/health')" || exit 1

CMD [".venv/bin/python", "-m", "ingest_worker.api.service"]
