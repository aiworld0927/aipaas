# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 仓库概览

AIPaaS 是面向金融标准化的知识库 RAG 问答平台，采用多语言 monorepo：

| 路径 | 技术栈 | 当前状态 |
| --- | --- | --- |
| `pymodules/` | Python 3.12、uv workspace、FastAPI、LangGraph | 当前活跃的后端 |
| `web/` | React 18 + Vite 5 + nginx | 当前活跃的前端 |
| `deploy/` | Docker Compose（基础层 + 应用层） | 当前部署方式 |
| `javaplat/` | 仅 Java/Spring 规划 | **无源码、无 pom.xml、无 Dockerfile**，不要尝试 `mvn` 构建 |
| `docs/` | Markdown 设计与部署文档 | 可参考，但部分内容已过时 |

重要文档偏差：根目录 `README.md` 中有大量**规划中但尚未落地**的目录（`contracts/`、`sdks/`、Gradle 版 `javaplat/`、`apps/ai-service`、`packages/core` 等），实际并不存在。请以真实目录、`pymodules/configs/`、`deploy/docker/docker-compose*.yaml` 和 `docs/02c_python模块开发部署.md` 为准，不要相信根 README 的规划树，也不要相信 `web/README.md` / `docs/05_待办事项.md` 中较旧的端口号。

## 常用命令

### Python（`pymodules/`）

```bash
cd pymodules
uv sync --all-packages --group cpu     # CPU 机器（GPU 机器用 --group gpu，CUDA 12.8）
uv lock --check
```

`--group` 必须显式指定，且 `cpu` / `gpu` 互斥。裸 `uv sync` 只会安装根项目，而根项目不声明任何依赖。`torch` / `flagembedding` / `pymilvus[model]` 只放在根 `[dependency-groups]` 中，**禁止**写入某个成员的 `pyproject.toml`，否则会破坏 uv 的索引解析。

本地配置：复制 `pymodules/.env.example` 为 `.env`，设置 `APP_ENV`（`devhome` / `devcompany` / `dev_win10` / `dev_win11`）和 `CONFIG_DIR=configs`。中间件地址变量通过 `source deploy/scripts/setup-env.sh devcompany`（或 `devhome`）导出。

启动服务（在 `pymodules/` 下执行）：

```bash
uv run uvicorn app_ingest_worker.api.service:app --reload
uv run uvicorn app_query_worker.api.service:app --reload
uv run uvicorn model_embedding_worker.api.service:app --reload
uv run uvicorn model_reranker_worker.api.service:app --reload
# 或：uv run python -m app_query_worker.api.service
```

当前默认端口：导入 **8001**、查询 **8002**、嵌入 **8003**、重排 **8004**。部分文档/README 仍写 8000/8001，以 YAML、Dockerfile、Compose 为准。改端口时优先设置 `APP_INGEST_WORKER_PORT` / `APP_QUERY_WORKER_PORT` / `MODEL_EMBEDDING_WORKER_PORT` / `MODEL_RERANKER_WORKER_PORT`，不要只传 `--port`：Nacos 注册读取的是 `settings.module.port`，两者必须一致。

检查与测试（仓库没有 pytest 测试套件）：

```bash
cd pymodules
uv run python -m compileall -q apps/app-ingest-worker/app_ingest_worker apps/app-query-worker/app_query_worker packages
uv run python tests/01-llm-test.py          # tests/ 下是手工冒烟脚本，需要真实中间件
uv run python tests/08-sse-test.py
uv run python -m unittest packages/common/tests/test_settings.py   # 唯一的 unittest 文件；fixture 已过时，当前存在失败
```

`tests/` 是按场景组织的手工验证脚本，不是断言型测试；多数依赖已启动的中间件、模型服务和真实 API Key。根目录 `pyrightconfig.json` 为 `pymodules` 配置了 Pyright（排除 `apps/apis`、`apps/eval-runner`）。

Docker 构建（构建上下文为 `pymodules/`）：

```bash
docker build -f apps/app-query-worker/Dockerfile -t aipaas/app-query-worker .
docker build -f apps/app-ingest-worker/Dockerfile -t aipaas/app-ingest-worker .
```

构建两个模型 worker 镜像前，必须先用 `docker/base/Dockerfile` 构建共享依赖基座（`TORCH_VARIANT=cpu|gpu`）。基座中记录了 `uv.lock` 的哈希；若 lock 已变更但基座未重建，应用镜像构建会直接失败。

### 前端（`web/`）

```bash
cd web
npm ci
npm run dev      # http://localhost:5173
npm run build    # 产物在 dist/
npm run preview
```

Vite 开发代理：`/query-api` → `127.0.0.1:8002`，`/import-api` → `127.0.0.1:8001`。生产环境由 nginx 在容器启动时用 `envsubst` 渲染 `nginx.conf.template`，注入 `QUERY_API_UPSTREAM` / `IMPORT_API_UPSTREAM`。

### 部署（`deploy/`）

```bash
source deploy/scripts/setup-env.sh devcompany   # 或 devhome；导出主机、JDK/Maven、数据卷目录等
cd deploy/docker
cp .env.example .env
# 第 4 级及以上必须先在 .env 配置 PLATFORM_BOOTSTRAP_ADMIN_PASSWORD

docker compose -f docker-compose-base.yaml up -d
docker compose -f docker-compose-models.yaml up -d --build
docker compose -f docker-compose-python.yaml up -d --build
docker compose -f docker-compose-java.yaml up -d --build
docker compose up -d --build                     # 第 5 级：默认全量入口
docker compose logs -f platform-gateway platform-management
```

五级 `include` 链依次为 base（核心 etcd / MinIO / Milvus / MongoDB / PostgreSQL / Redis，可选 attu / Nacos / RocketMQ / vLLM）→ models（init-app-dirs、嵌入、重排）→ python（app-ingest-worker、app-query-worker）→ java（幂等 platform-db-init、platform-management、platform-gateway）→ 主文件（frontend）。每一级都可独立作为启动入口并自动包含下层；base 无需 `--build`，可选组件仍由 `COMPOSE_PROFILES` 或 `--profile` 控制。

Java 侧已拆分为两个可部署应用，共享 `libs/common`：`platform-management` 是管理服务（登录/刷新/注销、用户与 RBAC、知识库、文档、审计落库、内容策略、系统配置、导入任务，并持有完整密钥与数据库/Flyway/MinIO 依赖），`platform-gateway` 是统一入口（本地 HS256 验签 + Redis 用户安全快照与 JTI 撤销校验、外部身份头清理与可信身份头重建、`/api/v1/**` 流式代理、问答安全编排、审计事件发布、Trace/幂等/SSE）。原 `platform-api` 聚合单体已删除，不再存在该可部署应用。

管理链路为 `frontend → platform-gateway → platform-management`，问答链路为 `frontend → platform-gateway → app-query-worker`（`/api/v1/chat`、`/api/v1/chat/stream` 由网关直接调用），导入链路为 `frontend → platform-gateway → platform-management → app-ingest-worker`。frontend 仅将 `/api/` 反代到 `PLATFORM_API_UPSTREAM=http://platform-gateway:8080`，不再保留直连 Python 的 `/query-api`、`/import-api` 旁路；HTTP / HTTPS 默认端口为 80 / 443，HTTPS 由 `FRONTEND_HTTPS_PORT` 配置，证书通过共享 `platform-tls` 卷提供。`docker-compose-p0.yaml` 保持独立 P0 编排，不并入五级链。

认证与状态同步：管理服务启动时以 0600 权限原子导出 Base64 JWT 验证密钥到 `/var/lib/aipaas/jwt-verify/jwt-secret`，网关只读挂载卷 `platform-jwt-verify`，不挂载 AES/审计密钥或业务数据卷。Redis 键为用户快照 `aipaas:gateway:auth:{userId}`、JTI 撤销 `aipaas:gateway:revoked:{jti}`、内容策略快照 `aipaas:gateway:content-policies`、审计 Stream `aipaas:gateway:audit`。用户/角色/内容策略变更采用 fail-closed（先失效旧快照，再改库，提交后重新发布），快照缺失或 Redis 故障时网关返回 401/503；网关把审计事件同步 `XADD` 到 Stream，管理服务消费并在成功落库/告警后才 ACK，失败保持 pending 重试（至少一次）。

## 架构说明

### Python workspace 分层

`apps/*` 是可独立部署的 FastAPI 服务；`packages/*` 是共享库。依赖方向严格单向：`apps/* → packages/*`，共享包禁止反向 import 应用。

当前共享包（前者是分发包名，后者是 import 时的下划线包名）：

- `packages/common` → `aipaas_common`：配置加载、日志、请求上下文、SSE 队列、内存任务状态、提示词、通用工具。最底层，不依赖其他同级包。
- `packages/model-clients` → `aipaas_model_clients`：LLM（`langchain-openai`）以及嵌入/重排 worker 的 HTTP 客户端。
- `packages/retrieval` → `aipaas_retrieval`：Milvus 混合检索、RRF 输入、集合工具。
- `packages/storage` → `aipaas_storage`：MongoDB 对话历史、MinIO、MySQL/SQLAlchemy、Kubernetes。
- `packages/nacos` → `aipaas_nacos`：Nacos 注册生命周期 + 接口元数据。
- `packages/mcp-agents1.0` → `aipaas_mcp_agents`：app-query-worker 使用的 MCP 联网搜索客户端。
- `packages/mcp-agents2.0` **不在** workspace 中，当前无人使用。

非 workspace 成员：`apps/apis`（管理端/平台侧，暂不处理）、`apps/eval-runner`（未适配）、`packages/mcp-agents2.0`。不要指望 `uv sync` 会安装它们。

新增代码时：

- 应用内部一律使用命名空间包：import 必须写 `app_ingest_worker.*` / `app_query_worker.*`，禁止顶层 `graph.*` / `api.*`——两个应用共用一个 venv，会互相遮蔽。
- 第三方依赖声明在**对应成员**的 `pyproject.toml` 中；包间依赖使用 `{ workspace = true }`，并通过 `uv add --package <dist-name>` 添加。
- 业务编排先留在 `apps/`，稳定后再上移到新的/现有 `packages/*` 成员（新成员必须有独立的 `pyproject.toml` 并加入根 workspace 的 `members`）。

### 配置系统（`aipaas_common.conf.settings`）

每个服务入口必须在**导入业务模块之前**调用 `load_settings("<module_name>")`；失败即终止启动，且按进程缓存。一个进程只能加载一个模块命名空间。

- `CONFIG_DIR` 指向 `pymodules/configs`（容器内挂载为 `/app/configs`）。
- `APP_ENV` 选择 `aipaas_config.{APP_ENV}.yaml`，叠加在 `aipaas_config.base.yaml` 之上（环境文件只写偏离项）。
- 解析 `${oc.env:...}` 前只保留 `common` + 当前模块命名空间，因此其他模块缺少密钥不会影响本进程。
- Pydantic schema 使用 `extra="forbid"`：新增 YAML 键必须同步在 `packages/common/aipaas_common/conf/schemas/*.py` 中加字段；新增可部署模块必须在 `MODULE_SCHEMAS` 中注册 schema。
- YAML 存放非密钥默认值；密钥通过 `${oc.env:VAR,null}` 从环境变量注入。
- `{MODULE_NAME大写}_HOST` / `{MODULE_NAME大写}_PORT` 可覆盖 YAML 中的监听地址。

### 服务启动模式

入口用 `AppLifespan` 组装 FastAPI 生命周期（按注册顺序启动、逆序关闭）。仅当 `common.nacos.enabled` 为真时添加 Nacos 注册；注册在后台任务中执行，先做 TCP 探测，**失败只告警不阻断启动**。注册到 Nacos 的实例元数据会自动包含 FastAPI 路由清单。

每个 HTTP 请求由中间件生成带前缀的 request_id 并写入上下文（`ingest_<uuid>` / `query_<uuid>`）。

### 导入流程（`apps/app-ingest-worker`）

`POST /upload` 将每个文件保存到 `pymodules/output_files/YYYYMMDD/<task_id>/`，尽力上传到 MinIO，随后用后台 asyncio 任务启动 `KBImportWorkflow`。客户端轮询 `GET /status/{task_id}`。

LangGraph 节点顺序（`app_ingest_worker/graph/main.py`）：
`node_entry` →（PDF 走 `node_pdf_to_md` → `node_md_img`，MD 直接走 `node_md_img`）→ `node_document_split` → `node_item_name_recognition` → `node_bge_embedding` → `node_import_milvus`。

链路涉及的外部依赖：MinerU API（PDF→MD）、多模态 LLM（图片理解）、Mongo/Milvus（主体名存储）、嵌入 worker、MinIO、Milvus。

### 查询流程（`apps/app-query-worker`）

`POST /query` 在非流式模式下直接 await 图执行；流式模式下创建 asyncio 任务并立即返回 `session_id`，客户端再订阅 `GET /stream/{session_id}`。节点把 SSE `DELTA`/`FINAL` 事件推入**进程内**队列。API 实际使用的是 `main_v2.KBQueryWorkflowV2`（`main_v1.py` 为遗留代码，未使用）。

LangGraph 节点顺序（`app_query_worker/graph/main_v2.py`）：
`node_item_name_confirm` → 并行分发到 `node_search_embedding`、`node_search_embedding_hyde`、`node_web_search_mcp` → `node_rrf` → `node_rerank` → `node_answer_output`。

若主体名确认阶段已直接产出答案，则路由直接进入答案输出节点。流程使用 Milvus 混合检索（BGE-M3 dense+sparse）、HyDE、MCP 联网搜索、RRF 融合、HTTP 重排，最后由 LLM 流式生成；对话历史存入 MongoDB（`chat_message`，按 `session_id` 组织）。

跨服务的限制：SSE 队列和任务状态（`aipaas_common.utils.task_utils`）都保存在进程内存中。查询/导入服务实际上只能单进程运行，多副本之间不会共享流、任务状态或上传进度。此外当前没有认证/授权，`session_id` 由客户端提供——在依赖隔离性之前必须先核实。

### 模型服务

`model-embedding-worker` 和 `model-reranker-worker` 对外暴露稳定的 HTTP API，与后端实现解耦：

- `POST /v1/embeddings` → dense + sparse 向量；`POST /v1/rerank` → 相关性分数。
- 配置项 `backend: offline|online` 决定走本地模型（`model_service.py`）还是远端接口（`online_service.py`）；在线嵌入后端只返回 dense。
- `/health` 是存活探针，`/ready` 是就绪探针（离线模型加载失败时返回 503）。
- 业务侧只通过 `aipaas_model_clients` 读取 `common.embedding_service.url` / `common.rerank_service.url`，不感知当前后端。

### 前端（`web/`）

React SPA，路由为 `/login`、`/chat`、`/chat/:sessionId`、`/import`、`/user`。API 调用集中在 `src/api/`，使用相对路径 `/query-api`、`/import-api`，因此开发代理和生产 nginx 共用同一套代码。由于后端没有认证/会话接口，登录态和会话列表目前是 `src/utils/` 下的 localStorage Mock。`src/components/` 中有 `RequireAuth` 路由守卫和统一布局；答案/SSE 解析在 `src/utils/answerParser.js`。

### 部署拓扑

Compose 项目网络为 `aipaas-network`（bridge）。容器之间通过服务名互访（`milvus:19530`、`minio:9000`、`mongodb:27017`、`svc-model-embedding:8003`、`platform-gateway:8080`、`platform-management:8080`）。部署拓扑为五级 `include` 链：`docker-compose-base.yaml` → `docker-compose-models.yaml` → `docker-compose-python.yaml` → `docker-compose-java.yaml` → `docker-compose.yaml`，分别对应技术组件、模型接入、Python 业务、Java 平台（management + gateway）和前端；`docker-compose-p0.yaml` 是独立 P0 编排，不属于该链。环境变量优先级：shell 已导出变量（`source deploy/scripts/setup-env.sh`）> `.env` > Compose 内置默认值。`configs/` 目录以只读方式挂载进每个 Python 容器。

## 改动前必查的坑

- 旧文档中的端口号不可信。当前默认值：导入 8001、查询 8002、嵌入 8003、重排 8004；platform-gateway 宿主 8081（`PLATFORM_GATEWAY_PORT`）/ 容器 8080，platform-management 仅容器内 8080、不映射宿主机；前端 nginx HTTP 80 / HTTPS 443（`FRONTEND_HTTPS_PORT` 可改）。
- 配置拼写错误会快速失败（`extra="forbid"`），但只校验当前进程加载的命名空间；改配置后请用 `compileall` 和服务启动验证。
- `load_settings` 必须先于那些在 import 阶段调用 `get_settings()` 的模块执行；沿用 `api/service.py` 中已有的入口顺序。
- 不要手工编辑 `uv.lock`；使用 `uv add` / `uv lock`。lock 变更后必须重建模型共享基座镜像。
- `pymodules/tests/` 不是 CI 测试套件，不要当可靠的回归覆盖。
- `javaplat/` 当前有两个可部署入口：`platform-management`（管理服务）与 `platform-gateway`（统一入口网关），共享 `libs/common`；原 `platform-api` 聚合单体已拆分删除。它们使用核心 PostgreSQL 中独立的 `PLATFORM_DB`（默认 `aipaas`），管理链路为 gateway → management，问答由 gateway 直连 app-query-worker，导入为 gateway → management → app-ingest-worker；第 4 级及以上启动前必须配置 `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`。
- 未解决的安全问题记录在 `docs/07_风险问题.md`（无认证、CORS 全放开、上传文件名路径穿越、MinIO 桶可匿名读取）。不要基于这些问题已修复的假设开发新行为。
