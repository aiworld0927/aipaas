# ===========================================================================
# AIPaaS Python 依赖基座镜像（CPU / GPU 两个变体，由 TORCH_VARIANT 决定）
# ---------------------------------------------------------------------------
# 作用：把「Python + uv + 全部第三方依赖」预装进 /app/.venv，供 model-embedding-worker
#      与 model-reranker-worker 共享，避免两个工程各自重复下载/解包 torch（约 12GB）。
#
# 构建（在 pymodules/ 目录下执行，必须先于 worker 镜像）：
#   docker build --network host --build-arg TORCH_VARIANT=gpu -f docker/base/Dockerfile -t aipaas/aipaas-torch-gpu-base:local .
#    
#   docker build --network host --build-arg TORCH_VARIANT=cpu \
#     -f docker/base/Dockerfile -t aipaas/aipaas-torch-cpu-base:local .
#
# 必须加 --network host：download.pytorch.org / pypi.nvidia.cn 在 bridge 网络下会超时。
#
# 依赖范围：pyproject.toml 的 [dependency-groups] cpu|gpu + workspace 全部成员的第三方
#          依赖；不安装任何本地包（aipaas-common / 各 worker 由应用镜像自行 -e 安装）。
#
# 与 uv.lock 的一致性：构建时把 lock 的 sha256 写入 /app/.uv-lock-sha256，
#          应用镜像构建时会校验，lock 变更后必须重建本镜像，否则直接失败。
# ===========================================================================

ARG TORCH_VARIANT=gpu

FROM python:3.12-slim

ARG TORCH_VARIANT

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_ROOT=/app \
    UV_HTTP_TIMEOUT=120 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# 只依赖 pyproject.toml + uv.lock 即可完成解析（--no-install-workspace 不读成员源码）
COPY pyproject.toml uv.lock ./

# 安装全部第三方依赖：
#   --group ${TORCH_VARIANT}  cpu|gpu 互斥分组（torch / torchvision / torchaudio / flagembedding / pymilvus[model]）
#   --all-packages            纳入所有 workspace 成员的依赖（fastapi / uvicorn / openai ...）
#   --no-install-workspace    不安装任何本地包，保持基座与业务代码解耦
#   --frozen                  严格按 uv.lock 安装，不重新解析
RUN case "${TORCH_VARIANT}" in cpu|gpu) ;; \
      *) echo "TORCH_VARIANT 只能是 cpu 或 gpu，收到: ${TORCH_VARIANT}" >&2; exit 1 ;; \
    esac && \
    echo ">>> Building with TORCH_VARIANT=${TORCH_VARIANT}" && \
    echo ">>> uv sync --frozen --no-dev --all-packages --no-install-workspace --group ${TORCH_VARIANT}" && \
    uv sync --frozen --no-dev --all-packages --no-install-workspace --group "${TORCH_VARIANT}"

# 校验 torch 构建与变体一致（cpu → torch.version.cuda is None；gpu → CUDA 12.8）
RUN TORCH_VARIANT="${TORCH_VARIANT}" /app/.venv/bin/python -c "\
import os, torch; \
v = os.environ['TORCH_VARIANT']; \
cuda = torch.version.cuda; \
assert (cuda is not None and cuda.startswith('12.8')) if v == 'gpu' else cuda is None, \
    f'TORCH_VARIANT={v} 但实际 torch={torch.__version__} cuda={cuda}'; \
print(f'base torch OK: {torch.__version__} (variant={v})')"

# 构建后端：应用镜像用 --no-build-isolation 离线安装本地包，需要 hatchling 已在环境内
RUN uv pip install --python /app/.venv/bin/python hatchling editables

# 记录 uv.lock 指纹，供应用镜像构建时校验（防止基座与 lock 漂移）
RUN /app/.venv/bin/python -c "\
import hashlib; \
print(hashlib.sha256(open('uv.lock','rb').read()).hexdigest(), end='')" > /app/.uv-lock-sha256

LABEL org.aipaas.image.role="python-deps-base" \
      org.aipaas.torch.variant="${TORCH_VARIANT}"

RUN mkdir -p /app/logs /app/models
VOLUME ["/app/logs", "/app/models"]
