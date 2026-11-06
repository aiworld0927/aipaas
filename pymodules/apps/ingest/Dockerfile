# ===========================================================================
# AIPaaS 导入服务 Dockerfile（独立部署单元，端口 8000）
# ---------------------------------------------------------------------------
# 多阶段构建：
#   stage1: uv sync 依赖（构建依赖树）
#   stage2: 运行时镜像（python-slim + 已 sync 的 venv）
#
# 构建：
#   cd pymodules
#   docker build -f apps/import/Dockerfile -t svc-import .
#
# 运行：
#   docker run -d -p 8000:8000 svc-import
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
COPY apps/ingest ./apps/ingest

# 只 sync svc-import + workspace 依赖树（--no-dev 跳过测试依赖）
RUN uv sync --package svc-ingest --no-dev 2>&1

# ---- 阶段 2：运行时 ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELSCOPE_CACHE=/app/models/modelscope_cache \
    HF_HOME=/app/models/huggingface \
    PROJECT_ROOT=/app \
    PYTHONPATH=/app/apps/ingest \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# path_util.py 用 .env 标识项目根，容器里放一个空文件即可
RUN touch .env

# 从 builder 拷 venv（所有依赖都在里面）
COPY --from=builder /app/.venv .venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制源码
COPY libs/common/aipaas_common ./libs/common/aipaas_common
COPY apps/ingest ./apps/ingest
COPY apps/ingest/pyproject.toml ./apps/ingest/pyproject.toml
COPY pyproject.toml uv.lock ./

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 入口：svc-ingest 的 FastAPI（PYTHONPATH 指向 apps/ingest，让 graph/config 可直接 import）
CMD [".venv/bin/python", "-m", "uvicorn", \
     "api.service:app", \
     "--host", "0.0.0.0", "--port", "8000"]
