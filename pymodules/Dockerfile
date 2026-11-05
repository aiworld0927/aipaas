# ===========================================================================
# AIPaaS pymodules FastAPI 服务镜像（直接 COPY .venv 版）
# ---------------------------------------------------------------------------
# 设计思想：不再在 Dockerfile 里装 uv / 重新 sync 依赖；
#          直接把宿主机已经生成好的 .venv 整体 COPY 进镜像。
#          构建速度极快，镜像体积 ≈ 宿主机 .venv + 源码。
#
#   ⚠️  关键前提（必须遵守，否则容器启动即崩）
#   ----------------------------------------------------------------
#   .venv 必须在 **同一平台同一架构的 Linux 机器** 上生成，然后再 build。
#   Windows 开发机上 uv 创建的 .venv（Scripts/ 目录、*.pyd 扩展）
#   无法在 Linux 容器里执行。正确姿势：
#
#     # CPU 部署机器 / GPU 部署机器上分别执行：
#     cd pymodules
#     uv sync --group cpu      # CPU 部署机
#     uv sync --group gpu      # GPU 部署机
#     docker build -t aipaas-pymodules:local .
#     docker run ... aipaas-pymodules:local
#
#   即：在哪台机器上 build，就在哪台机器上先 uv sync，.venv 与镜像平台严格一致。
# ===========================================================================

# 只提供 Python 运行时；不装 uv、不装 git、不装 ca-certificates，一切依赖都在 .venv 里
FROM python:3.12-slim

# ---- 环境变量 ----
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 统一模型缓存路径，运行时靠 -v 挂载出来
    MODELSCOPE_CACHE=/app/models/modelscope_cache \
    HF_HOME=/app/models/huggingface

# ---- 工作目录 ----
WORKDIR /app

# ---- 1/3 依赖：直接把宿主机 uv sync 好的 .venv 全量搬进来 ----
# （.dockerignore 里不能再排除 .venv，否则会被过滤掉）
COPY .venv .venv

# ---- 2/3 源码 ----
COPY app ./app
COPY conf ./conf
COPY prompts ./prompts
COPY pyproject.toml uv.lock ./

# ---- 3/3 运行时 ----
EXPOSE 8000

# 容器里直接用 .venv 内的 Python 解释器跑 uvicorn
# 用 ".venv/bin/python -m uvicorn" 而不是 ".venv/bin/uvicorn"，
# 避免 .venv/pyvenv.cfg 里 home 路径不一致时入口脚本找不到 Python
ENV PATH="/app/.venv/bin:$PATH"

# 健康检查：FastAPI 根路由 GET /
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# 入口：app.apis.aipaas_main:app（FastAPI 实例）
CMD [".venv/bin/python", "-m", "uvicorn", \
     "app.apis.aipaas_main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
