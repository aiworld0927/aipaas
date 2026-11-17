# ===========================================================================
# AIPaaS 重排序模型服务镜像（bge-reranker-large，端口 8004）
# ---------------------------------------------------------------------------
# 依赖全部来自基座镜像，本文件只叠加业务代码：
#   aipaas/aipaas-torch-cpu-base:<tag>   torch==2.11.0+cpu
#   aipaas/aipaas-torch-gpu-base:<tag>   torch==2.11.0+cu128
#
# 构建（在 pymodules/ 目录下执行，先构建 docker/base/Dockerfile）：
#   docker build --network host \
#     --build-arg BASE_IMAGE=aipaas/aipaas-torch-gpu-base:local \
#     -f apps/model-reranker-worker/Dockerfile -t aipaas/svc-model-reranker:latest .
#
# 只改业务代码时本文件约 30 秒完成，且不需要联网。
# ===========================================================================

ARG BASE_IMAGE=aipaas/aipaas-torch-gpu-base:local

FROM ${BASE_IMAGE}

WORKDIR /app

# 基座与 uv.lock 一致性校验：lock 变了但基座没重建时直接失败，避免运行环境静默漂移
COPY uv.lock ./
RUN test "$(sha256sum uv.lock | cut -d' ' -f1)" = "$(cat /app/.uv-lock-sha256)" \
      || { echo "基座镜像与当前 uv.lock 不一致，请先重建 docker/base/Dockerfile" >&2; exit 1; }

COPY packages ./packages
COPY apps/model-reranker-worker ./apps/model-reranker-worker
COPY configs ./configs

# 只安装本地包，不解析、不下载（--no-deps），也不需要构建隔离环境
RUN uv pip install --python /app/.venv/bin/python --no-deps --no-build-isolation \
      -e packages/common -e apps/model-reranker-worker

EXPOSE 8004

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:${MODEL_RERANKER_WORKER_PORT:-8004}/health')" || exit 1

CMD [".venv/bin/python", "-m", "model_reranker_worker.api.app"]
