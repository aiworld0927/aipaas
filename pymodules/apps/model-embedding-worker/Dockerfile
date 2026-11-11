# ===========================================================================
# AIPaaS 嵌入模型服务 Dockerfile（BGE-M3，独立部署单元，端口 8101）
# ---------------------------------------------------------------------------
# 构建（在 pymodules/ 目录下执行，TORCH_VARIANT 决定 CPU / GPU 版）：
#   # GPU 镜像（CUDA 12.8，默认）
#   docker build --network host --build-arg TORCH_VARIANT=gpu \
#     -f apps/model-embedding-worker/Dockerfile -t aipaas/svc-model-embedding:latest .
#   # CPU 镜像（无 CUDA 依赖，体积小）
#   docker build --network host --build-arg TORCH_VARIANT=cpu \
#     -f apps/model-embedding-worker/Dockerfile -t aipaas/svc-model-embedding:cpu .
#
# 必须加 --network host：构建容器访问 download.pytorch.org / pypi.nvidia.cn
# 在 docker 默认 bridge 网络下会 ReadTimeout 或 network unreachable。
#
# 运行（模型目录挂载到容器内 /app/models/bge-m3；配置包外部映射到 /app/configs）：
#   docker run -d --name svc-model-embedding -p 8101:8101 --gpus all \
#     -v /host/models/bge-m3:/app/models/bge-m3:ro \
#     -v /host/path/logs:/app/logs \
#     -v /host/path/aipaas-configs:/app/configs:ro \
#     -e APP_ENV=devcompany -e CONFIG_DIR=/app/configs \
#     aipaas/svc-model-embedding:latest
# ===========================================================================

# ---- 阶段 1：依赖构建 ----
FROM python:3.12-slim AS builder

# torch 构建版本开关：cpu = pytorch.org/whl/cpu；gpu = pytorch.org/whl/cu128
# 取值由根 pyproject.toml 的 cpu / gpu 互斥分组（[dependency-groups]）决定
ARG TORCH_VARIANT=gpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=120

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# workspace 配置 + 依赖的成员源码（editable install 需要源码树）
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/model-embedding-worker ./apps/model-embedding-worker

# torch / flagembedding / pymilvus[model] 只声明在根 pyproject.toml 的 cpu / gpu 互斥分组里，
# 因此 sync 必须显式带 --group（这里统一用 cpu 分组，保证任何机器都能解析；
# gpu 变体在下一步把 torch 系替换成 cu128 构建）
RUN case "${TORCH_VARIANT}" in cpu|gpu) ;; \
      *) echo "TORCH_VARIANT 只能是 cpu 或 gpu，收到: ${TORCH_VARIANT}" >&2; exit 1 ;; \
    esac && \
    uv sync --package model-embedding-worker --no-dev --group cpu 2>&1

# gpu 变体：把 CPU 版 torch 系替换为 CUDA 12.8 构建（cpu 变体跳过此步）
# 注意：构建时必须加 --network host，否则 bridge 网络下访问 pypi.nvidia.cn 会 ReadTimeout
RUN if [ "${TORCH_VARIANT}" = "gpu" ]; then \
      uv pip install --python /app/.venv/bin/python --reinstall \
        --extra-index-url https://download.pytorch.org/whl/cu128 \
        torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128; \
    fi

# 校验实际装到的 torch 构建与 TORCH_VARIANT 一致：
# cpu 分组 torch.version.cuda 必须为 None；gpu 分组必须是 CUDA 12.8
RUN TORCH_VARIANT="${TORCH_VARIANT}" /app/.venv/bin/python -c "\
import os, torch; \
v = os.environ['TORCH_VARIANT']; \
cuda = torch.version.cuda; \
assert (cuda is not None and cuda.startswith('12.8')) if v == 'gpu' else cuda is None, \
    f'TORCH_VARIANT={v} 但实际 torch={torch.__version__} cuda={cuda}'; \
print(f'torch OK: {torch.__version__} (variant={v})')"

# ---- 阶段 2：运行时 ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_ROOT=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN touch .env && mkdir -p /app/logs /app/models

COPY --from=builder /app/.venv .venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY packages ./packages
COPY apps/model-embedding-worker ./apps/model-embedding-worker
COPY configs ./configs
COPY pyproject.toml uv.lock ./

# 数据卷声明：日志目录 + 模型目录（由 compose 或 -v 挂载到宿主机）
VOLUME ["/app/logs", "/app/models"]

EXPOSE 8101

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8101/health')" || exit 1

CMD [".venv/bin/python", "-m", "uvicorn", \
     "model_embedding_worker.api.service:app", \
     "--host", "0.0.0.0", "--port", "8101"]
