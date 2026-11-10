# AIPaaS Python AI 服务开发部署文档

> 适用对象：后端开发 / 运维 / DevOps
> 代码位置：`/coding/aipaas/pymodules/`
> 架构：uv workspace（apps + packages）
> Python 版本：≥ 3.12,< 3.14

---

## 1. 模块简介

### 1.1 整体架构

AIPaaS 的 AI 服务层采用 **uv workspace** 单体仓（monorepo）结构，将可独立运行的服务放在 `apps/`，将可被复用的能力放在 `packages/`。所有 workspace 成员共用一个 `.venv`（位于 `pymodules/.venv`），通过 **editable install** 实现源码修改即时生效。

```
pymodules/
├── pyproject.toml          # workspace 根（不声明 dependencies）
├── uv.lock                 # 统一锁文件
├── .venv/                  # 所有成员共用的虚拟环境
├── apps/                   # 可独立运行的应用（每个 app = 一个部署单元）
│   ├── ingest-worker/      # 文档导入服务（端口 8000）
│   └── query-worker/       # 用户查询服务（端口 8001）
├── packages/               # 共享能力包（被 apps 和 packages 向下依赖）
│   ├── common/             # 基础共享库
│   ├── llm/                # LLM 接入层
│   ├── retrieval/          # 检索层（Milvus / ES / Qdrant / Neo4j）
│   ├── storage/            # 存储层（Mongo / MinIO / MySQL / K8s）
│   ├── pipeline/           # RAG 流程编排基类
│   └── mcp-agents1.0/      # MCP 客户端（v1: mcp<2）
├── configs/                # 配置文件（prompts、pipelines、aipaas_config.yaml）
└── tests/                  # 开发测试脚本（不纳入 workspace）
```

> **不在 workspace 中但存在于代码库的目录**：`apps/apis/`（管理端业务逻辑）、`apps/eval-runner/`（RAG 评估脚本）、`packages/mcp-agents2.0/`（mcp≥2 版本，暂未被任何成员依赖）。这些目录独立管理，不参与 `uv sync --all-packages`。

### 1.2 依赖方向（严格遵守，禁止反向）

```
apps（svc-ingest / svc-query）
  └─► packages
        ├─► aipaas-retrieval
        │     └─► aipaas-llm
        │           └─► aipaas-common
        ├─► aipaas-storage
        │     └─► aipaas-common
        ├─► aipaas-pipeline
        │     └─► aipaas-common
        └─► aipaas-mcp-agent1
              └─► aipaas-common
```

**核心约束**：
- `packages/` 内的包**禁止** import `apps/` 中的任何模块。
- 下层包（如 `common`）**禁止** import 上层包（如 `llm`、`retrieval`）。
- 新增依赖必须在被依赖方的 `pyproject.toml` 中声明，并通过 `[tool.uv.sources]` 指向 `{ workspace = true }`。

### 1.3 各 app 职责

| App | 端口 | 职责 | 入口模块 |
|-----|------|------|----------|
| `svc-ingest`（`apps/ingest-worker`） | 8000 | 接收文档上传 → LangGraph 流程编排（PDF转MD → 图片处理 → 文档切片 → 主体识别 → BGE 向量化 → 写入 Milvus） | `ingest_worker.api.service:app` |
| `svc-query`（`apps/query-worker`） | 8001 | 接收用户查询 → LangGraph 流程编排（主体确认 → 向量检索 / HyDE 检索 / MCP Web 搜索 → RRF 融合 → 重排 → SSE 流式输出） | `query_worker.api.service:app` |

### 1.4 各 package 职责

| Package | Python 包名 | 职责 |
|---------|-------------|------|
| `packages/common` | `aipaas_common` | 基础共享：日志（loguru）、路径、SSE 工具、任务管理、配置管理（OmegaConf）、请求 ID 上下文 |
| `packages/llm` | `aipaas_llm` | LLM 接入：DashScope / Ollama / 百炼、BGE 嵌入、重排序、LangChain 封装 |
| `packages/retrieval` | `aipaas_retrieval` | 检索层：Milvus、Elasticsearch、Qdrant、Neo4j 客户端 + 重排 HTTP 工具 |
| `packages/storage` | `aipaas_storage` | 存储层：MongoDB（含历史记录）、MinIO、MySQL、Kubernetes 客户端 |
| `packages/pipeline` | `aipaas_pipeline` | RAG 流程编排基类 |
| `packages/mcp-agents1.0` | `aipaas_mcp_agents` | MCP 客户端（v1 版本：`mcp>=1.19.0,<2` + `openai-agents<0.20.0`） |

---

## 2. 开发环境搭建

### 2.1 前置条件

- Python ≥ 3.12（仓库根 `.python-version` 已锁定）
- [uv](https://docs.astral.sh/uv/)（包管理器，替代 pip / poetry）
- IDE（推荐 PyCharm 或 VS Code）

### 2.2 安装 workspace 全部依赖

```bash
cd pymodules

# ⚠️ 必须加 --all-packages！
# 裸 uv sync 只会装根项目（aipaas-monorepo），而根项目没有 dependencies，
# 结果就是一个空 .venv，import 全部报 ModuleNotFoundError。
uv sync --all-packages

# 校验锁文件是否与 pyproject.toml 一致（CI 也会跑）
uv lock --check

# 冒烟测试：import 全部 workspace 成员
uv run python -c "import aipaas_common, aipaas_llm, aipaas_retrieval, aipaas_storage, aipaas_pipeline, aipaas_mcp_agents, ingest_worker, query_worker; print('ok')"
```

### 2.3 IDE 解释器配置

IDE 解释器必须指向 **`pymodules/.venv/bin/python`**。

- **PyCharm**：Settings → Project → Python Interpreter → Add → Existing → `pymodules/.venv/bin/python`
- **VS Code**：`Ctrl+Shift+P` → "Python: Select Interpreter" → 选择 `pymodules/.venv/bin/python`

> 由于所有 workspace 成员都是 **editable install**，`packages/` 或 `apps/` 里的源码修改会在重启服务后即时生效，无需重新 `uv sync`。

### 2.4 辅助脚本

```bash
# 查看当前 workspace 成员
uv workspace list

# 锁定所有成员的依赖变更
uv lock && uv sync --all-packages
```

---

## 3. 开发模式启动服务

所有命令均在 `pymodules/` 目录下执行，共用 `.venv`。

### 3.1 同时启动两个服务（不同终端）

```bash
# 终端 1：导入服务（端口 8000）
cd pymodules
uv run uvicorn ingest_worker.api.service:app --host 0.0.0.0 --port 8000 --reload

# 终端 2：查询服务（端口 8001）
cd pymodules
uv run uvicorn query_worker.api.service:app --host 0.0.0.0 --port 8001 --reload
```

`--reload` 会监听源码变更并自动重启，开发时务必加上。

### 3.2 限定 workspace 成员执行

```bash
# 只加载 svc-ingest 及其依赖，避免 query-worker 的重依赖拖慢启动
uv run --package svc-ingest python -m ingest_worker.graph.main

# 或执行 svc-query
uv run --package svc-query python -c "from query_worker.api.service import app; print(app.title)"
```

### 3.3 环境变量

两个服务启动时依赖以下环境变量（开发环境可通过 `.env` 文件或直接 export）：

```bash
# Milvus（向量数据库；URL 形式，代码读取 MILVUS_URL）
export MILVUS_URL=http://localhost:19530

# MinIO（对象存储）
export MINIO_ENDPOINT=http://localhost:9000
export MINIO_ACCESS_KEY=minioadmin
export MINIO_SECRET_KEY=minioadmin
export MINIO_BUCKET_NAME=aipaas

# MongoDB（文档存储 + 会话历史）
export MONGO_URL=mongodb://admin:123456@localhost:27017/?authSource=admin
export MONGO_DB_NAME=aipaas

# LLM（外部模型服务；代码实际读取 OPENAI_BASE_URL / OPENAI_API_KEY / MODEL）
export OPENAI_BASE_URL=http://localhost:11434/v1
export OPENAI_API_KEY=ollama
export MODEL=qwen2.5

# 日志前缀（开发环境默认为 app，建议显式设置）
export LOG_FILE_PREFIX=ingest-worker   # 或 query-worker
```

> `.env` 文件可直接放在 `pymodules/` 下，`aipaas_common.core.log` 初始化时会通过 `load_dotenv()` 自动加载。

---

## 4. 依赖管理

### 4.1 新增第三方依赖到某个 workspace 成员

```bash
# 为 aipaas-retrieval 新增 elasticsearch
uv add --package aipaas-retrieval elasticsearch>=8.0.0

# 从 aipaas-llm 移除 dashscope
uv remove --package aipaas-llm dashscope

# 变更后务必重新锁定 + 同步
uv lock && uv sync --all-packages
```

### 4.2 workspace 内部新增依赖

如果一个成员要依赖另一个 workspace 成员，除了 `uv add` 之外，还需要确认被依赖方的 `pyproject.toml` 中有 `[tool.uv.sources]` 声明。uv 通常会自动处理，手动检查：

```toml
# 在依赖方的 pyproject.toml 中
[tool.uv.sources]
aipaas-common = { workspace = true }
aipaas-llm = { workspace = true }
```

### 4.3 新增包到 workspace

```bash
# 1. 建目录 + 写 pyproject.toml（参考 packages/common/pyproject.toml 模板）
mkdir -p packages/<new-pkg>/<new_pkg_name>
touch packages/<new-pkg>/<new_pkg_name>/__init__.py

# 2. 在根 pyproject.toml 的 [tool.uv.workspace].members 中追加路径
# 3. 重新锁文件并同步
uv lock && uv sync --all-packages
```

### 4.4 GPU 版 PyTorch

当前 `svc-ingest` / `svc-query` 默认走 CPU 版（`torch==2.11.0+cpu`，来自 `https://download.pytorch.org/whl/cpu`）。GPU 机部署时需将 index 切换为 CUDA 版本（如 `cu128`），参考各 app 的 `pyproject.toml` 中 `[tool.uv.index]` 配置。

---

## 5. Docker 镜像构建 & 单独运行

两个 app 各自拥有独立的 Dockerfile，采用 **多阶段构建**（builder 阶段装依赖 + runtime 阶段只拷贝 `.venv` 和源码），镜像体积小、构建可缓存。

### 5.1 构建镜像

```bash
# 必须在 pymodules/ 目录下执行，context 就是 pymodules/
cd pymodules

# 导入服务
docker build -f apps/ingest-worker/Dockerfile -t aipaas/svc-ingest:latest .

# 查询服务
docker build -f apps/query-worker/Dockerfile -t aipaas/svc-query:latest .
```

构建细节（以 ingest-worker 为例）：
- **builder 阶段**：`python:3.12-slim` + `uv`，执行 `uv sync --package svc-ingest --no-dev`（仅装生产依赖）
- **runtime 阶段**：新的 `python:3.12-slim`，只拷贝 `.venv` + `packages/` + `apps/ingest-worker/` + `configs/` + `pyproject.toml` + `uv.lock`
- 环境变量内置了生产默认值：`LOG_FILE_PREFIX=ingest-worker`、`PROJECT_ROOT=/app`、`MODELSCOPE_CACHE=/app/models/modelscope_cache` 等
- 声明了两个卷：`/app/logs`（日志）、`/app/models`（模型缓存）

### 5.2 单独运行（需要中间件已就绪）

```bash
# 运行 svc-ingest（端口 8000）
docker run -d --name svc-ingest -p 8000:8000 \
  --network host \
  -e MILVUS_URL=http://127.0.0.1:19530 \
  -e MINIO_ENDPOINT=http://127.0.0.1:9000 \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  -e MONGO_URL=mongodb://admin:123456@127.0.0.1:27017/?authSource=admin \
  -e MONGO_DB_NAME=aipaas \
  -e LOG_FILE_PREFIX=ingest-worker \
  -v /tmp/logs/svc-ingest:/app/logs \
  -v /tmp/models/svc-ingest:/app/models \
  aipaas/svc-ingest:latest

# 运行 svc-query（端口 8001）
docker run -d --name svc-query -p 8001:8001 \
  --network host \
  -e MILVUS_URL=http://127.0.0.1:19530 \
  -e MINIO_ENDPOINT=http://127.0.0.1:9000 \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  -e MONGO_URL=mongodb://admin:123456@127.0.0.1:27017/?authSource=admin \
  -e MONGO_DB_NAME=aipaas \
  -e LOG_FILE_PREFIX=query-worker \
  -v /tmp/logs/svc-query:/app/logs \
  -v /tmp/models/svc-query:/app/models \
  aipaas/svc-query:latest

# 查看日志
docker logs -f svc-ingest
docker logs -f svc-query
```

> 若中间件跑在 Docker 网络内而非 host，将 `--network host` 替换为自定义 bridge 网络，并将 `127.0.0.1` 改为中间件容器名。

---

## 6. docker compose 中部署

### 6.1 目录结构

```
deploy/docker/
├── .env.example               # 环境变量模板（拷贝为 .env 后修改）
├── docker-compose.yaml        # 主编排（include 基础编排；平台中间件 + Python 服务 + 前端）
└── docker-compose-base.yaml   # 基础编排（init-volumes + etcd / minio / milvus / attu / mongodb）
```

### 6.2 启动顺序

依赖链：`init-app-dirs` → `milvus（来自 base 编排，依赖 etcd + minio）/ minio / mongodb` → `svc-ingest / svc-query`。**编排依赖 base 编排的 milvus / minio / mongodb**，主编排通过 `depends_on`（`service_healthy`）等待它们就绪。

推荐启动步骤：

```bash
cd deploy/docker

# 1. 拷贝并修改环境变量
cp .env.example .env
vim .env         # 修改 MINIO 密码、MONGO 密码、LLM 地址等

# 2. 启动 Python AI 服务
#    depends_on 会自动拉起 base 编排的 milvus / minio / mongodb 及 init 容器，
#    并等待 milvus / minio healthy 后再启动服务
docker compose up -d svc-ingest svc-query

# 3. 查看状态
docker compose ps svc-ingest svc-query

# 4. 查看实时日志
docker compose logs -f svc-ingest
docker compose logs -f --tail=100 svc-query

# 5. 停止
docker compose down
```

> 也可以先单独启动依赖的中间件确认 healthy，再启动服务：
>
> ```bash
> docker compose up -d milvus minio mongodb
> docker compose ps milvus minio mongodb
> docker compose up -d svc-ingest svc-query
> ```

### 6.3 compose 内网络地址

compose 编排的服务之间**必须用容器名（service name）互相寻址**，不能用 `localhost` 或宿主机 IP。关键配置：

| 配置项 | compose 内正确值 | 说明 |
|--------|------------------|------|
| `MILVUS_URL` | `http://milvus:19530` | Milvus 地址（URL 形式，代码读取 MILVUS_URL，非 HOST/PORT 拆分）|
| `MINIO_ENDPOINT` | `http://minio:9000` | MinIO 容器名 |
| `MONGO_URL` | `mongodb://admin:123456@mongodb:27017/?authSource=admin` | MongoDB 连接串（账密与 base 编排 mongodb 服务一致）|
| `MONGO_DB_NAME` | `aipaas` | 业务库名 |
| `LLM_BASE_URL` | `http://host.docker.internal:11434/v1` | LLM 跑在宿主机时用此地址；代码实际读取 `OPENAI_BASE_URL`，compose 中由 `LLM_BASE_URL` 映射 |

compose 已为两个 Python 服务预填好这些地址，见 [docker-compose.yaml](file:///coding/aipaas/deploy/docker/docker-compose.yaml) 的 `svc-ingest` / `svc-query` 服务定义。

---

## 7. 日志配置详解

### 7.1 实现位置

[packages/common/aipaas_common/core/log.py](file:///coding/aipaas/pymodules/packages/common/aipaas_common/core/log.py)，基于 **loguru** 实现，在首次 `import aipaas_common.core.log` 时自动初始化全局 `logger`。

### 7.2 支持的环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `PROJECT_ROOT` | 自动推导（向上查找 `.env` 文件） | 项目根目录。容器内**必须**显式设置为 `/app`，否则自动推导会失败。 |
| `LOG_FILE_PREFIX` | `app` | 日志文件名前缀。各服务务必在启动时覆盖为服务名，否则不同服务的日志会写到同一个 `app_YYYYMMDD.log` 里。 |
| `LOG_FILE_ENABLE` | `True` | 是否启用文件输出。 |
| `LOG_FILE_LEVEL` | `INFO` | 文件日志级别。 |
| `LOG_FILE_RETENTION` | `7 days` | 日志保留策略，支持 loguru 的 retention 语法（如 `"14 days"`、`"30 days"`）。 |
| `LOG_CONSOLE_ENABLE` | `True` | 是否启用控制台输出。 |
| `LOG_CONSOLE_LEVEL` | `INFO` | 控制台日志级别。 |

> **注意**：代码中没有 `LOG_DIR` 环境变量。日志目录由 `PROJECT_ROOT` 硬编码推导为 `PROJECT_ROOT / "logs"`。如果需要改路径，设置 `PROJECT_ROOT` 即可。

### 7.3 日志文件命名规则

```
{LOG_FILE_PREFIX}_YYYYMMDD.log
```

示例：
- 开发环境（未设 LOG_FILE_PREFIX）：`pymodules/logs/app_20261110.log`
- ingest-worker 容器：`/app/logs/ingest-worker_20261110.log`
- query-worker 容器：`/app/logs/query-worker_20261110.log`

### 7.4 日志轮转与清理

- **轮转**：loguru 配置 `rotation="00:00"`，每天 0 点自动创建新文件。
- **清理**：loguru 根据 `LOG_FILE_RETENTION` 自动删除过期文件（默认 7 天）。
- **编码**：UTF-8，彻底解决中文乱码。
- **异步入队**：`enqueue=True`，支持多线程 / asyncio 场景不丢失日志。

### 7.5 容器内日志挂载

两个 Dockerfile 都声明了 `VOLUME ["/app/logs", "/app/models"]`。compose 进一步把 `/app/logs` 映射到宿主机目录：

```yaml
# docker-compose.yaml 中 svc-ingest 的 volumes
volumes:
  - ${DOCKER_LOG_DIRECTORY:-.}/logs/svc-ingest:/app/logs
```

宿主机日志目录由 `.env` 中的 `DOCKER_LOG_DIRECTORY` 控制，默认相对路径为 `deploy/docker/logs/svc-ingest/` 和 `deploy/docker/logs/svc-query/`。主编排的 `init-app-dirs` 会在启动前预创建这些目录并修正属主。

### 7.6 如何使用

在任何 workspace 成员里直接 import：

```python
from aipaas_common.core.log import logger

logger.info("导入任务开始", task_id=task_id)
logger.warning(f"文件上传 MinIO 失败，本地继续处理: {e}")
logger.exception("LangGraph 全流程执行失败")  # 自动带完整堆栈
```

日志还内置了 **request_id** 上下文支持（`inject_request_id` patch），通过 `aipaas_common.core.context.request_id_ctx_var.set(...)` 注入后，每条日志都会自动带上对应的 request_id。

---

## 8. 健康检查

| 服务 | 端点 | Dockerfile healthcheck | compose healthcheck |
|------|------|------------------------|---------------------|
| svc-ingest | `/health`（需开发补充） | ✅ 已声明（`curl localhost:8000/health`） | ✅ 已声明 |
| svc-query | `/health` ✅ | ✅ 已声明 | ✅ 已声明 |

> **注意**：截至当前版本，`svc-ingest` 的 FastAPI app（`ingest_worker/api/service.py`）**尚未显式注册 `/health` 端点**，但 Dockerfile 和 compose 的 healthcheck 已在探测此端点。部署后若 healthcheck 持续失败，需在 ingest-worker 的 `service.py` 中补充：
> ```python
> @app.get("/health")
> async def health():
>     return {"ok": True}
> ```
> `svc-query` 已正确实现，见 [query_worker/api/service.py](file:///coding/aipaas/pymodules/apps/query-worker/query_worker/api/service.py) 的第 183-188 行。

---

## 9. 新增共享包 / 新增应用包规范

### 9.1 在 `packages/` 新增共享包

假设新增包名为 `packages/vector-store/`，Python 包名为 `aipaas_vector_store`：

**步骤 1**：建目录

```bash
mkdir -p packages/vector-store/aipaas_vector_store
touch packages/vector-store/aipaas_vector_store/__init__.py
```

**步骤 2**：写 `pyproject.toml`（参考 `packages/pipeline/pyproject.toml`，最简单只依赖 `aipaas-common`）

```toml
[project]
name = "aipaas-vector-store"
version = "0.1.0"
description = "向量存储层"
requires-python = ">=3.12"
dependencies = [
    "aipaas-common",
]

[tool.uv.sources]
aipaas-common = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["aipaas_vector_store"]
```

**步骤 3**：根 `pyproject.toml` 追加 members

```toml
[tool.uv.workspace]
members = [
    ...
    "packages/vector-store",   # 新增
]
```

**步骤 4**：被依赖的包追加 `[tool.uv.sources]`

如果 `svc-ingest` 要依赖新包，执行：

```bash
uv add --package svc-ingest aipaas-vector-store
```

uv 会自动在 `apps/ingest-worker/pyproject.toml` 的 `[tool.uv.sources]` 中追加 `aipaas-vector-store = { workspace = true }`。

**步骤 5**：重新锁文件并同步

```bash
uv lock && uv sync --all-packages
```

**步骤 6**：校验

```bash
# 冒烟
uv run python -c "import aipaas_vector_store; print('ok')"
# 依赖方向校验（packages/ 下不得 import apps/）
grep -rn "from ingest_worker\|from query_worker\|import ingest_worker\|import query_worker" packages/
# 应该返回 0 行
```

### 9.2 在 `apps/` 新增应用包

假设新增 `apps/search-worker/`：

**步骤 1-5**：同新增包流程（目录 → pyproject.toml → 根 workspace members → uv lock & sync）。

**步骤 6**：编写 Dockerfile（参考 ingest-worker / query-worker 的双阶段模板）

**步骤 7**：`deploy/docker/docker-compose.yaml` 追加服务定义（build context、environment、ports、volumes、healthcheck、depends_on）。关键要素：
- 环境变量中必须显式设置 `LOG_FILE_PREFIX=<服务名>` 和 `PROJECT_ROOT=/app`
- 日志卷挂载 `${DOCKER_LOG_DIRECTORY}/logs/<服务名>:/app/logs`
- healthcheck 指向 `/health`
- depends_on 声明依赖的 base 编排中间件（milvus / minio / mongodb，`service_healthy`）与主编排的 `init-app-dirs`（`service_completed_successfully`）

**步骤 8**：在 FastAPI app 中实现 `/health` 端点。

---

## 10. 测试与验证清单

### 10.1 编译检查

```bash
cd pymodules
uv run python -m compileall -q packages/ apps/ingest-worker/ apps/query-worker/
# 无输出 = 全部通过
```

### 10.2 import 冒烟测试

```bash
# 只测 workspace 内的正式成员
uv run python -c "
import aipaas_common
import aipaas_llm
import aipaas_retrieval
import aipaas_storage
import aipaas_pipeline
import aipaas_mcp_agents
import ingest_worker
import query_worker
print('all imports ok')
"
```

### 10.3 依赖方向校验（禁止 packages → apps 反向）

```bash
cd pymodules
# 正向：packages 内的包不得 import apps 目录
grep -rn "from ingest_worker\|from query_worker\|import ingest_worker\|import query_worker" packages/
# 理想输出：0 行

# 正向：下层包不得 import 上层包
grep -rn "from aipaas_llm\|from aipaas_retrieval" packages/common/
grep -rn "from aipaas_retrieval" packages/llm/
# 理想输出：0 行
```

### 10.4 /health 端点

```bash
# 启动服务后
curl -s http://localhost:8001/health    # query-worker → {"ok": true}
curl -s http://localhost:8000/health    # ingest-worker → 应返回 {"ok": true}（待补充实现）
```

### 10.5 日志文件验证

```bash
# 开发环境启动 ingest-worker
export LOG_FILE_PREFIX=ingest-worker
uv run uvicorn ingest_worker.api.service:app --port 8000 &

# 查看日志文件是否按预期命名
ls -la pymodules/logs/ingest-worker_*.log
tail -f pymodules/logs/ingest-worker_*.log
```

### 10.6 Docker 镜像构建验证

```bash
cd pymodules
docker build -f apps/ingest-worker/Dockerfile -t test-ingest . 2>&1 | tail -20
# 最终应看到 "Successfully tagged test-ingest:latest"
```

---

## 11. 常见问题

### Q1：`ModuleNotFoundError: No module named 'aipaas_common'`

**原因**：忘记加 `--all-packages`，裸 `uv sync` 只装根项目（空依赖）。

**修复**：
```bash
cd pymodules
uv sync --all-packages
uv run python -c "import aipaas_common; print('ok')"
```

### Q2：日志文件名还是 `app_20261110.log` 而不是 `ingest-worker_20261110.log`

**原因**：没设置 `LOG_FILE_PREFIX`。默认值是 `app`。

**修复**：
```bash
export LOG_FILE_PREFIX=ingest-worker   # 或在 .env 中写
uv run uvicorn ingest_worker.api.service:app --port 8000
```

### Q3：compose 内 milvus 连接失败，报 `Connection refused`

**原因**：环境变量里写了 `localhost:19530` 或 `127.0.0.1:19530`。在 Docker 网络内，服务之间必须用 **容器名**（service name）寻址。

**修复**：compose 已预填正确值。若手动覆盖，确保：
- `MILVUS_URL=http://milvus:19530`
- `MINIO_ENDPOINT=http://minio:9000`
- `MONGO_URL=mongodb://admin:123456@mongodb:27017/?authSource=admin`

### Q4：容器启动后日志目录不存在或为空

**原因**：`PROJECT_ROOT` 未设置，log.py 的自动推导在容器内会失败；或 `/app/logs` 卷未正确挂载。

**修复**：compose 已自动设置 `PROJECT_ROOT=/app` 并挂载日志卷。若手动 `docker run`，务必加：
```bash
-e PROJECT_ROOT=/app \
-v /host/logs/svc-ingest:/app/logs
```

### Q5：模型下载慢，每次重建镜像都要重新下

**原因**：Dockerfile 声明了 `MODELSCOPE_CACHE=/app/models/modelscope_cache` 和 `HF_HOME=/app/models/huggingface`，但没挂载卷，每次新容器都是空的。

**修复**：挂载模型缓存卷到宿主机持久化：
```bash
docker run ... \
  -v /host/models/svc-ingest:/app/models \
  aipaas/svc-ingest:latest
```

compose 中也可追加：
```yaml
svc-ingest:
  volumes:
    - ${DOCKER_LOG_DIRECTORY}/logs/svc-ingest:/app/logs
    - ${DOCKER_MODEL_DIRECTORY:-./models}/svc-ingest:/app/models
```

### Q6：`uv lock --check` 失败

**原因**：有人改了 `pyproject.toml` 里的依赖但忘了重新 `uv lock`。

**修复**：
```bash
uv lock && uv sync --all-packages
```

### Q7：ingest-worker 的 compose healthcheck 一直是 `starting` / `unhealthy`

**原因**：`ingest_worker/api/service.py` 中尚未实现 `/health` 端点（截至当前版本）。

**修复**：在 ingest-worker 的 FastAPI app 中补充：
```python
@app.get("/health")
async def health():
    return {"ok": True}
```

### Q8：GPU 机部署时 PyTorch 报 CUDA not available

**原因**：默认 `torch==2.11.0+cpu` 是 CPU 版。

**修复**：修改 `apps/ingest-worker/pyproject.toml` 和 `apps/query-worker/pyproject.toml` 中 `[tool.uv.index]` 的 URL 为 CUDA 版本（如 `https://download.pytorch.org/whl/cu128`），并将 torch 依赖切换为对应版本，然后 `uv lock && uv sync --all-packages`。

---

## 附录：快速参考卡

```bash
# ---------- 开发环境 ----------
cd pymodules && uv sync --all-packages     # 一次性装好全部
uv lock --check                             # 校验锁文件

# ---------- 开发启动 ----------
uv run uvicorn ingest_worker.api.service:app --port 8000 --reload
uv run uvicorn query_worker.api.service:app  --port 8001 --reload

# ---------- 依赖变更 ----------
uv add    --package aipaas-retrieval elasticsearch>=8.0.0
uv remove --package aipaas-llm dashscope
uv lock && uv sync --all-packages

# ---------- 镜像构建 ----------
cd pymodules
docker build -f apps/ingest-worker/Dockerfile -t aipaas/svc-ingest .
docker build -f apps/query-worker/Dockerfile  -t aipaas/svc-query  .

# ---------- compose ----------
cd deploy/docker
docker compose up -d svc-ingest svc-query   # 自动拉起依赖的 base 中间件（milvus/minio/mongodb）
docker compose logs -f svc-ingest
```
