# ===========================================================================
# AIPaaS 查询服务 Dockerfile（独立部署单元，端口 8001）
# ---------------------------------------------------------------------------
# 构建：
#   cd pymodules
#   docker build -f apps/query/Dockerfile -t svc-query .
# ===========================================================================

# ---- 阶段 1：依赖构建 ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELSCOPE_CACHE=/app/models/modelscope_cache \
    HF_HOME=/app/models/huggingface

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# 先复制 workspace 配置 + 源码 → 让 editable install 正常构建
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY apps/query ./apps/query

# 只 sync svc-query + workspace 依赖树
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

# path_util.py 用 .env 标识项目根，容器里放一个空文件即可
RUN touch .env

# 从 builder 拷 venv
COPY --from=builder /app/.venv .venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制源码
COPY libs/common/aipaas_common ./libs/common/aipaas_common
COPY apps/query/app ./apps/query/app
COPY apps/query/pyproject.toml ./apps/query/pyproject.toml
COPY pyproject.toml uv.lock ./

EXPOSE 8001

# 健康检查（query 服务有 /health 端点）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

# 入口：svc-query 的 FastAPI
CMD [".venv/bin/python", "-m", "uvicorn", \
     "apps.query.app.api.service:app", \
     "--host", "0.0.0.0", "--port", "8001"]
