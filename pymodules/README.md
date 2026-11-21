# pymodules（Python Monorepo）

AIPaaS 的 Python AI 服务工程，采用 **uv workspace monorepo** 管理：多个可独立部署的业务应用与分层共享包共用同一份锁文件、同一个虚拟环境 `pymodules/.venv`。

> 当前 P0 已新增 `packages/pipeline`、`packages/retrieval` 的金融标准检索能力和 `packages/generation`；`apps` 下可部署模块为 `ingest-worker`（svc-ingest）、`query-worker`（svc-query）与 `model-*-worker`。下文迁移记录保留历史状态，模块成员以 `pyproject.toml` 为准，跨语言集成边界以[总架构说明](../docs/03_架构说明.md)为准。

本文档是**总文档**，负责说明工程总体情况与公共包；每个可独立部署的模块拆分为独立文档，详见下方导航。

## 文档导航（总—分）

| 层级 | 文档 | 覆盖内容 |
| --- | --- | --- |
| 总 | [README.md](README.md)（本文） | 总体架构、目录结构、workspace、配置体系、公共包、部署与测试 |
| 分 | [docs/ingest-worker.md](docs/ingest-worker.md) | 文档导入服务 `svc-ingest`：接口、导入图、节点、MinIO/Milvus、镜像 |
| 分 | [docs/query-worker.md](docs/query-worker.md) | 知识库查询服务 `svc-query`：接口、查询图、SSE、商品名确认、重排 |
| 分 | [docs/model-embedding-worker.md](docs/model-embedding-worker.md) | 嵌入模型服务：BGE-M3 / 在线后端、接口、torch 基座镜像 |
| 分 | [docs/model-reranker-worker.md](docs/model-reranker-worker.md) | 重排模型服务：BGE-Reranker / DashScope、接口、基座镜像 |
| 分 | [docs/eval-runner.md](docs/eval-runner.md) | 离线 RAG 评估脚本：流程、指标、适配状态 |
| 分 | [docs/apis.md](docs/apis.md) | 平台侧公共 API：部署/镜像/容器接口、历史代码现状 |

## 服务一览

| 服务 | 目录 | workspace 成员 | 端口 | 职责 |
| --- | --- | --- | --- | --- |
| 文档导入 | `apps/ingest-worker` | 是（`svc-ingest`） | 8001 | 文件上传、解析、切分、商品名识别、向量化、入库（LangGraph 编排） |
| 知识库查询 | `apps/query-worker` | 是（`svc-query`） | 8002 | 商品名确认、检索、RRF、重排、答案生成、SSE 流式输出 |
| 嵌入模型 | `apps/model-embedding-worker` | 是 | 8003 | BGE-M3 / 在线 Embedding 独立推理服务（dense + sparse） |
| 重排模型 | `apps/model-reranker-worker` | 是 | 8004 | BGE-Reranker / DashScope TextReRank 独立推理服务 |
| 离线评测 | `apps/eval-runner` | 否 | — | ragas 评测脚本（由根目录 `eval/` 迁入，待适配新目录） |
| 公共 API | `apps/apis` | 否 | 8000 | 平台侧 FastAPI（部署/镜像/容器管理，当前暂不处理） |

端口默认值来自 `configs/aipaas_config.base.yaml`，可用 `{MODULE_NAME大写}_PORT` 环境变量覆盖。

## 当前目录结构

```
pymodules/                                  # Python monorepo（uv workspace 根，共用 .venv）
├── pyproject.toml                          # workspace 根：只声明 members 与 cpu/gpu 分组
├── uv.lock                                 # 全仓统一锁文件
├── .python-version                         # Python 3.12
├── .env.example                            # 环境变量模板（APP_ENV / CONFIG_DIR / 密钥）
├── README.md                               # 总文档（总体 + 公共包）
│
├── docs/                                   # 分文档：每个独立部署模块一篇
│   ├── ingest-worker.md
│   ├── query-worker.md
│   ├── model-embedding-worker.md
│   ├── model-reranker-worker.md
│   ├── eval-runner.md
│   └── apis.md
│
├── apps/                                   # 可独立部署的业务应用
│   ├── ingest-worker/                      # name = svc-ingest
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── ingest_worker/                  # ★ 应用内命名空间包
│   │       ├── api/                        # app / dependencies / import_service / routers
│   │       └── graph/                      # main / state / nodes（a~g）
│   ├── query-worker/                       # name = svc-query
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── query_worker/                   # ★ 应用内命名空间包
│   │       ├── api/                        # app / dependencies / query_service / routers
│   │       └── graph/                      # main_v1 / main_v2 / state / nodes
│   ├── model-embedding-worker/             # 嵌入推理服务
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── model_embedding_worker/         # api / service / model_service / online_service
│   ├── model-reranker-worker/              # 重排推理服务
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── model_reranker_worker/          # api / service / model_service / online_service
│   ├── eval-runner/                        # 离线评测（未纳入 workspace）
│   │   ├── eval.py
│   │   ├── qa.csv
│   │   └── eval_result.csv
│   └── apis/                               # 平台侧公共 API（未纳入 workspace）
│       ├── aipaas_main.py
│       ├── aipaas_lifespan.py
│       ├── lifespan.py
│       ├── pymod.py
│       ├── routers/                        # deploysvc / item / user
│       └── svc/                            # 镜像、容器、端口、任务、SSH、K8s、MySQL 等
│
├── packages/                               # 分层共享库（除 mcp-agents2.0 外均为 workspace 成员）
│   ├── common/                             # aipaas-common：配置 / 日志 / 上下文 / 任务 / SSE / 工具
│   │   └── aipaas_common/
│   │       ├── conf/                       # settings.py + schemas（公共与各模块命名空间）
│   │       ├── core/                       # log / context / middleware / lifespan / background / exceptions / load_prompt
│   │       ├── utils/                      # sse / task / path / table / rate_limit / ip / format / normalize_sparse_vector
│   │       └── log_decorator.py            # node_log / step_log
│   ├── model-clients/                      # aipaas-model-clients：LLM / 嵌入 HTTP / 重排 HTTP
│   ├── retrieval/                          # aipaas-retrieval：Milvus 适配
│   ├── storage/                            # aipaas-storage：MongoDB / MinIO / Kubernetes（MySQL 依赖预留）
│   ├── nacos/                              # aipaas-nacos：Nacos 服务注册/注销
│   ├── mcp-agents1.0/                      # aipaas-mcp-agent1：mcp<2 + openai-agents<0.20.0
│   └── mcp-agents2.0/                      # aipaas-mcp-agent2：mcp 2.x（不在 workspace）
│
├── configs/                                # 配置与提示词
│   ├── aipaas_config.base.yaml             # 基线配置（公共命名空间 + 各模块默认值）
│   ├── aipaas_config.devhome.yaml          # 家中环境覆盖
│   ├── aipaas_config.devcompany.yaml       # 公司环境覆盖
│   ├── aipaas_config.dev_win10.yaml        # Windows 开发环境覆盖
│   ├── aipaas_config.dev_win11.yaml        # Windows 开发环境覆盖
│   ├── admin.conf                          # 历史遗留配置
│   └── prompts/rag/*.prompt                # 提示词模板（版本化）
├── docker/base/Dockerfile                  # torch 依赖基座镜像（CPU/GPU）
├── logs/                                   # 运行日志（gitignore）
└── tests/                                  # 全仓验证脚本（按序号/场景组织，非 pytest 用例）
```

## 分层与依赖约定

```
apps/*  ──►  packages/*
                  ▲
        ┌─────────┼──────────┬──────────┐
   retrieval   storage    model-clients  nacos / mcp-agents
        └─────────┴──────────┴──────────┘
                  │
                common
```

- **单向依赖**：`apps/* → packages/*`，`packages/*` 禁止反向依赖 `apps/*`
- `packages/common` 是最底层，不依赖其他共享包
- `packages/model-clients` 只依赖 `common`；`retrieval` 依赖 `common` + `model-clients`；`storage` / `nacos` / `mcp-agents` 只依赖 `common`
- `ingest-worker` 依赖 `common / model-clients / nacos / retrieval / storage`
- `query-worker` 在此基础上额外依赖 `mcp-agents1.0`（联网搜索）
- 两个模型推理服务只依赖 `common`，模型运行时（torch / FlagEmbedding）由根 workspace 的 `cpu|gpu` 分组提供
- 应用内一律使用命名空间包（`ingest_worker.*` / `query_worker.*` / `model_embedding_worker.*` / `model_reranker_worker.*`），**禁止**使用 `graph.*`、`api.*` 这类顶层名——多个应用共存时会发生包名遮蔽
- 所有第三方依赖在**成员** `pyproject.toml` 声明；根 `pyproject.toml` 不声明依赖
- 服务型应用声明 `[tool.hatch.build.targets.wheel] packages = [...]`，保证 editable 安装后模块名全局唯一

## 统一虚拟环境

所有成员共用 `pymodules/.venv`，不要在成员目录里单独 `uv venv`。

```bash
cd pymodules
uv sync --all-packages --group cpu    # CPU 机器
uv sync --all-packages --group gpu    # NVIDIA GPU 机器（CUDA 12.8）
```

- 必须带 `--all-packages`，裸 `uv sync` 只同步根项目。
- 必须带 `--group cpu` 或 `--group gpu`：`torch / torchvision / torchaudio / flagembedding / pymilvus[model]` 只在这两个互斥分组中，缺失会导致模型服务无法运行。
- 两组互斥，同时勾选会直接报错，避免装出 CPU+GPU 混杂环境。

## 快速开始

```bash
cd pymodules
cp .env.example .env          # 填写密钥后使用
uv sync --all-packages --group cpu

# 文档导入服务 http://127.0.0.1:8001
uv run python -m ingest_worker.api.app

# 知识库查询服务 http://127.0.0.1:8002
uv run python -m query_worker.api.app

# 嵌入模型服务 http://127.0.0.1:8003
uv run python -m model_embedding_worker.api.app

# 重排模型服务 http://127.0.0.1:8004
uv run python -m model_reranker_worker.api.app
```

入口统一使用 `python -m`：模块入口会先 `load_settings(<module_name>)`，再导入业务模块。若用 uvicorn，请显式指定 `--port` 并保证与配置/环境变量一致：

```bash
uv run uvicorn query_worker.api.app:app --port 8002 --reload
```

容器构建（在 `pymodules` 目录执行）：

```bash
# 两个业务服务
docker build -f apps/query-worker/Dockerfile  -t aipaas/svc-query  .
docker build -f apps/ingest-worker/Dockerfile -t aipaas/svc-ingest .

# 两个模型服务：先构建 torch 基座，再构建应用镜像
docker build --network host --build-arg TORCH_VARIANT=gpu \
  -f docker/base/Dockerfile -t aipaas/aipaas-torch-gpu-base:local .
docker build --network host --build-arg BASE_IMAGE=aipaas/aipaas-torch-gpu-base:local \
  -f apps/model-embedding-worker/Dockerfile -t aipaas/svc-model-embedding .
docker build --network host --build-arg BASE_IMAGE=aipaas/aipaas-torch-gpu-base:local \
  -f apps/model-reranker-worker/Dockerfile -t aipaas/svc-model-reranker .
```

## 配置说明

### 加载流程

统一由 [settings.py](packages/common/aipaas_common/conf/settings.py) 的 `load_settings(module_name)` 完成：

1. `load_dotenv()`：读取 `.env`，`override=False`，真实环境变量优先。
2. 校验 `module_name` 已在 `MODULE_SCHEMAS` 注册（取值即 Python 包名）。
3. 读取 `CONFIG_DIR`（代码默认 `pymodules/configs`，可用环境变量覆盖）。
4. 校验 `APP_ENV`：`devhome / devcompany / dev_win10 / dev_win11`，默认 `devhome`。
5. 合并 `aipaas_config.base.yaml` → `aipaas_config.{APP_ENV}.yaml`，两个文件必须存在。
6. **先裁切**：只保留 `common` + 本模块命名空间。
7. **后 resolve**：`${oc.env:...}` 只在裁切后的子树上解析，其他模块缺失的密钥不影响本进程。
8. Pydantic 校验（`extra="forbid"`，密钥为 `Optional[SecretStr]`，消费方运行时校验）。
9. 按进程缓存；同一进程换 `module_name` 直接报错。
10. 按 `common.logging` 与本模块名重建日志 sink，并打印裁切后的键集合（不含值）。
11. 用 `{MODULE_NAME大写}_HOST/PORT` 覆盖监听地址（环境变量 > 环境 YAML > base.yaml）。

共享包不感知 `module_name`，通过 `get_settings()` 读取本进程已加载的配置。

### 配置文件

| 文件 | 作用 |
| --- | --- |
| `configs/aipaas_config.base.yaml` | 公共命名空间与四个模块的默认值（容器拓扑口径） |
| `configs/aipaas_config.devhome.yaml` | 家中开发环境覆盖（模型路径、CPU、fp16=false） |
| `configs/aipaas_config.devcompany.yaml` | 公司开发环境覆盖 |
| `configs/aipaas_config.dev_win10.yaml` / `dev_win11.yaml` | Windows 开发环境覆盖 |
| `configs/prompts/rag/*.prompt` | 提示词模板，由 `aipaas_common.core.load_prompt` 加载 |

`common` 命名空间包含：`llm`、`milvus`、`minio`、`mongo`、`mineru`、`mcp`、`nacos`、`embedding_service`、`rerank_service`、`logging`。各模块命名空间与字段见对应分文档。

### 环境变量

| 变量 | 说明 |
| --- | --- |
| `APP_ENV` | 环境标识：`devhome / devcompany / dev_win10 / dev_win11` |
| `CONFIG_DIR` | 配置目录（默认 `pymodules/configs`） |
| `OPENAI_API_KEY` | LLM、多模态、MCP 通用密钥 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 凭据 |
| `MONGO_URL` | MongoDB 连接串（含账密） |
| `MINERU_API_TOKEN` | MinerU 文档解析令牌 |
| `EMBEDDING_ONLINE_API_KEY` / `TEXT_RERANK_API_KEY` | 模型服务在线后端独立密钥（缺省回退 `OPENAI_API_KEY`） |
| `EMBEDDING_SERVICE_URL` / `RERANKER_SERVICE_URL` | 业务侧模型服务地址 |
| `NACOS_ENABLED` / `NACOS_SERVER_ADDR` / `NACOS_NAMESPACE` / `NACOS_GROUP` / `NACOS_USERNAME` / `NACOS_PASSWORD` / `NACOS_REGISTER_IP` | Nacos 注册 |
| `INGEST_WORKER_HOST/PORT`、`QUERY_WORKER_HOST/PORT`、`MODEL_EMBEDDING_WORKER_HOST/PORT`、`MODEL_RERANKER_WORKER_HOST/PORT` | 覆盖各服务监听地址 |
| `PROJECT_ROOT` | 项目根目录（容器内为 `/app`） |

常用业务阈值已收敛到 `query_worker` 命名空间（`query_high_threshold`、`query_mid_threshold`、`rerank_max_topk`、`rerank_min_topk`、`rerank_gap_abs`、`rerank_gap_ratio`、`max_context_chars`），不再通过散落的 `os.getenv` 读取。

## 公共包部分

### packages/common（aipaas-common）

最底层共享库，提供配置、日志、上下文、生命周期、后台任务、SSE、任务追踪与通用工具。

| 模块 | 内容 |
| --- | --- |
| `conf/settings.py` | `load_settings` / `get_settings` / `SettingsError`，配置合并、裁切、环境覆盖、日志重建 |
| `conf/schemas/common.py` | `CommonSettings` 及 `LLM / Milvus / Minio / Mongo / Mineru / Mcp / Nacos / HttpService / Logging` 子模型 |
| `conf/schemas/{module}.py` | `IngestSettings`、`QuerySettings`、`ModelEmbeddingSettings`、`ModelRerankerSettings`，由 `MODULE_SCHEMAS` 注册 |
| `core/log.py` | 基于 loguru；控制台/文件双输出，文件 `<前缀>_YYYYMMDD.log`，自动注入 `request_id`，`enqueue=True` |
| `core/context.py` | `request_id_ctx_var`（ContextVar，默认 `backend_script`） |
| `core/middleware.py` | `install_request_id_middleware`：为每个请求生成 `{prefix}_{uuid}` |
| `core/lifespan.py` | `AppLifespan`：组合多个组件 lifespan，按注册顺序启动、逆序退出，失败自动回滚 |
| `core/background.py` | `BackgroundTaskManager`：持有后台协程强引用，退出时等待/取消 |
| `core/exceptions.py` | `BusinessError`：可预期业务失败，日志不打印堆栈 |
| `core/load_prompt.py` | 从 `configs/prompts/rag/<name>.prompt` 读取并 `str.format` 渲染 |
| `log_decorator.py` | `node_log` / `step_log`：兼容 sync/async，自动维护运行中/已完成节点与耗时 |
| `utils/sse_utils.py` | `SSEEvent`、`SSEHub`、`default_sse_hub`、`push_to_session`、`sse_generator` |
| `utils/task_utils.py` | `TaskTracker`、状态常量、节点中文名映射、进程内默认实例函数 |
| `utils/path_util.py` | `get_path_dir`、`get_project_root`、`PROJECT_ROOT` |
| `utils/table_utils.py` | 表格/文本块处理辅助 |
| `utils/rate_limit_utils.py` | 限流辅助 |
| `utils/ip_util.py` | 本机 IP 探测 |
| `utils/format_utils.py` | 格式化辅助 |
| `utils/normalize_sparse_vector.py` | 稀疏向量归一化 |

测试：`packages/common/tests/test_settings.py`。

### packages/model-clients（aipaas-model-clients）

模型能力客户端，只依赖 `common`，不包含本地推理：

- `lm/lm_utils.py`：`get_llm_client(model=None, json_mode=False)`，带全局缓存的 `ChatOpenAI`，关闭千问 `enable_thinking`，支持 JSON 输出模式。
- `lm/llm_client.py`：`get_vl_client()`，多模态客户端。
- `lm/embedding_http_utils.py`：`generate_embeddings` / `agenerate_embeddings`，调用 `common.embedding_service.url` 的 `/v1/embeddings`，把 JSON 字符串 key 的稀疏向量还原为 int key。
- `lm/reranker_http_utils.py`：`rerank_documents` / `arerank_documents`，调用 `common.rerank_service.url` 的 `/v1/rerank`，返回与输入等长的分数列表。
- MCP 客户端不在此包，见 `packages/mcp-agents1.0`。

### packages/retrieval（aipaas-retrieval）

Milvus 适配层（历史规划中的 ES / Qdrant / Neo4j 未实现）：

- `get_milvus_client()`：Milvus 客户端单例，连接失败返回 `None`。
- `fetch_chunks_by_chunk_ids(...)`：按主键批量取切片，`chunk_id` 统一转 INT64，过滤无效 ID。
- `create_hybrid_search_requests(...)`：构造稠密/稀疏两路 `AnnSearchRequest`，支持 `expr` 与 `limit`。
- `hybrid_search(...)`：`WeightedRanker` 加权融合，支持 `norm_score`、输出字段与搜索参数。
- `escape_milvus_string(value)`：转义反斜杠与双引号，防止 filter 解析失败。

### packages/storage（aipaas-storage）

存储与基础设施客户端：

- `minio_utils.py`：懒加载 `Minio` 客户端、建桶与公开读策略、`aupload_file_to_minio`（线程池包装同步 SDK）。
- `mongo_history_utils.py`：`HistoryMongoTool` 单例、`save_chat_message`、`get_recent_messages`、`update_message_item_names`、`clear_history`，含 ObjectId JSON 编码。
- `kubernetes_client_manager.py`：`K8sClient`，封装 kubeconfig/context/连接池与常用资源操作。
- `pyproject.toml` 预留 `sqlalchemy`（MySQL），当前无 MySQL 实现文件。

### packages/nacos（aipaas-nacos）

- `register_current_service(service_name, app)`：注册临时实例；`common.nacos` 缺失、`enabled=false`、服务端不可达或注册异常时返回 `None`，不阻断启动。
- `resolve_instance_ip()`：优先 `common.nacos.register_ip`，否则用 UDP connect 探测默认出口网卡。
- `collect_interface_metadata(app)`：把路由（path/methods/summary）写入实例 metadata，同时提供 `interface_count` 与 `openapi_path`。
- `nacos_lifespan(service_name)`：启动后台注册、退出注销；注册任务不阻塞服务启动。
- 依赖 Nacos 官方 Python SDK 的 v2 异步 API。

### packages/mcp-agents1.0 / mcp-agents2.0

- `mcp-agents1.0`（distribution `aipaas-mcp-agent1`，Python 包 `aipaas_mcp_agents`）：`mcp>=1.19.0,<2` + `openai-agents<0.20.0`，被 `query-worker` 使用。
  - `call_web_search_mcp(rewritten_query, max_retries=3)`：使用 `MCPServerStreamableHttp` 连接百炼 MCP，调用 `bailian_web_search`（`count=2`），失败按 1/2/4 秒指数退避，最终抛错。
- `mcp-agents2.0`（distribution `aipaas-mcp-agent2`）：`mcp>=2,<3` + `openai-agents==0.23.1`，**不在 workspace**，暂未被任何成员依赖，独立管理。

## 接口一览

| 服务 | 主要接口 | 详细文档 |
| --- | --- | --- |
| 导入（8001） | `POST /upload`、`GET /status/{task_id}`、`GET /import.html`、`GET /health` | [docs/ingest-worker.md](docs/ingest-worker.md) |
| 查询（8002） | `POST /query`、`GET /stream/{session_id}`、`GET/DELETE /history/{session_id}`、`GET /health` | [docs/query-worker.md](docs/query-worker.md) |
| 嵌入（8003） | `POST /v1/embeddings`、`GET /v1/model/info`、`GET /health`、`GET /ready` | [docs/model-embedding-worker.md](docs/model-embedding-worker.md) |
| 重排（8004） | `POST /v1/rerank`、`GET /v1/model/info`、`GET /health`、`GET /ready` | [docs/model-reranker-worker.md](docs/model-reranker-worker.md) |
| 平台 API（8000） | `/ImageList`、`/PackageSearch`、`/PackageSearchNew`、`/ImageConstruct`、`/ImageConstruct_pypi`、`/TargetSelect`、`/items`、`/users` | [docs/apis.md](docs/apis.md) |

## 部署与镜像

- **业务服务**（ingest / query）：`python:3.12-slim` 两阶段构建；builder 用 `uv sync --frozen --no-dev --all-packages --no-install-workspace` 装第三方依赖，runtime 再 editable 安装源码；健康检查 `/health`。
- **模型服务**（embedding / reranker）：复用 [docker/base/Dockerfile](docker/base/Dockerfile) 的 torch 基座（CPU 或 CUDA 12.8），应用镜像只做源码复制与 `-e` 安装；构建时校验 `uv.lock` 的 sha256，lock 变更后必须先重建基座。
- 所有服务均支持 Nacos 注册（`common.nacos.enabled=false` 时跳过），注册失败只告警。
- 详细构建参数、数据卷与环境变量见各模块文档。

## 测试脚本

`tests/` 下为按场景组织的验证脚本，直接以脚本方式运行（非 pytest 用例）：

```bash
uv run python tests/01-llm-test.py
uv run python tests/08-sse-test.py
uv run python tests/14-test-setting.py
uv run python tests/15-test-nacos-client.py
uv run python tests/16-test-config-load.py
```

其他脚本覆盖 LangGraph 编译/调用、CUDA 可用性、MCP、日志、Kubernetes、定时任务、Word 域解析、类方法等场景。

## 与前端对接

- `/query-api/**` → `http://127.0.0.1:8002/**`
- `/import-api/**` → `http://127.0.0.1:8001/**`

详见 [web/README.md](../web/README.md)。

## 目标规划（未全部落地）

参考企业级 RAG 目录规划，以下能力**尚未建设**，当前不做空壳占位：

| 规划项 | 状态 | 说明 |
| --- | --- | --- |
| `packages/core` | 未建 | 独立领域模型 / Protocol / ABC；当前基础能力在 `packages/common/core` |
| `packages/ingestion` | 未建 | parsers / cleaners / chunkers；当前解析逻辑仍在 `ingest_worker/graph` |
| `packages/embeddings` | 未建 | 嵌入适配独立包；当前客户端在 `packages/model-clients`，推理在 `apps/model-embedding-worker` |
| `packages/observability` | 未建 | OTel / token 计量；当前仅有 `common/core/log` |
| `packages/eval` | 未建 | 指标 / 数据集 / LLM-as-judge |
| `packages/platform-sdk` | 未建 | 回调 Java 平台的 client（依赖 contracts/openapi） |
| `apps/embedding-service` | 已落地 | 由 `apps/model-embedding-worker` 提供 |
| `apps/reranker-service` | 已落地 | 由 `apps/model-reranker-worker` 提供 |
| `configs/pipelines` | 未建 | 原规划为声明式 pipeline 占位，当前目录不存在 |
| `apps/apis` | 保留 | 平台侧公共 API，按约定暂不处理 |

拆分原则：**业务编排代码先留在 apps，等能力稳定后再上移到 packages**，避免为目录规划产生空包与假边界。

## 迁移记录（2026-11-08）

| 变更 | 原因 |
| --- | --- |
| `libs/common` → `packages/common` | 共享库纳入 `packages/` 分层 |
| `clients/lm`、`embedding_client_manager`、`mcp_client*` → `packages/llm` | LLM 能力独立成包 |
| `milvus/es/qdrant/neo4j/reranker_http` → `packages/retrieval` | 检索能力独立成包 |
| `mongo/mysql/minio/kubernetes` → `packages/storage` | 存储与基础设施客户端独立成包 |
| `rag_base.py` → `packages/pipeline` | 编排基类独立成包 |
| 应用源码收进 `ingest_worker/`、`query_worker/` | 消除两个应用 `graph`/`api` 顶层包名冲突 |
| `prompts/`、`conf/` → `configs/` | 配置与提示词单一根目录 |
| 根 `eval/` → `apps/eval-runner/` | 评测执行器归入 apps |
| 删除空目录 `app/`、`libs/` | 迁移后清理 |

后续结构演进（记录当前真实状态，与上表并不冲突）：

| 变更 | 原因 |
| --- | --- |
| `packages/llm` → `packages/model-clients` + `packages/mcp-agents1.0` | LLM/嵌入/重排客户端与 MCP 客户端解耦，避免 mcp 版本冲突 |
| `packages/pipeline` 移除 | 无实际实现，避免空包 |
| 嵌入/重排推理上移为 `apps/model-embedding-worker`、`apps/model-reranker-worker` | 模型进程与业务进程隔离，GPU 资源独立调度 |
| `naocs` 服务注册独立为 `packages/nacos` | 注册能力复用，职责单一 |
| 配置由 `apps/*/config.py` 改为统一 `conf/schemas` + YAML | 配置可校验、可裁切、密钥不落盘 |
| 新增 `docker/base/Dockerfile` | 两个模型服务共享 torch 基座，避免重复下载约 12GB 依赖 |

## 文档拆分记录（2026-11-17）

按「总—分」结构拆分，原有内容不删减，仅重排与按模块下沉：

| 文档 | 内容来源 |
| --- | --- |
| `README.md` | 总体部分 + 公共包部分（原 README 全部章节保留并修正端口、路径、包名） |
| `docs/ingest-worker.md` | 导入服务的接口、图、节点、部署细节 |
| `docs/query-worker.md` | 查询服务的接口、图、SSE、商品名确认、重排、部署细节 |
| `docs/model-embedding-worker.md` | 嵌入服务的后端、接口、配置、基座镜像 |
| `docs/model-reranker-worker.md` | 重排服务的后端、接口、配置、基座镜像 |
| `docs/eval-runner.md` | 评测脚本流程、指标、适配状态与已知问题 |
| `docs/apis.md` | 平台 API 的入口、路由、svc 模块与历史现状 |
