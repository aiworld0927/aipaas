# pymodules（Python Monorepo）

AIPaaS 的 Python AI 服务工程，采用 **uv workspace monorepo** 管理：多个可独立部署的业务应用与分层共享包共用同一份锁文件、同一个虚拟环境 `pymodules/.venv`。

## 服务一览

| 服务 | 目录 | 端口 | 职责 |
| --- | --- | --- | --- |
| 文档导入 | `apps/ingest-worker` | 8000 | 文件上传、解析、切分、向量化、入库（LangGraph 编排） |
| 知识库查询 | `apps/query-worker` | 8001 | 检索、重排、答案生成、SSE 流式输出 |
| 公共 API | `apps/apis` | — | 平台侧 FastAPI（当前暂不处理，未纳入 workspace） |
| 离线评测 | `apps/eval-runner` | — | 评测脚本（由根目录 `eval/` 迁入，待适配新目录） |

## 当前目录结构

```
pymodules/                                  # Python monorepo（uv workspace 根，共用 .venv）
├── pyproject.toml                          # workspace 根：只声明 members，不装依赖
├── uv.lock                                 # 全仓统一锁文件
├── .python-version                         # Python 3.12
├── .gitignore                              # 忽略 .venv / logs / data
│
├── apps/                                   # 可独立部署的业务应用（workspace 成员）
│   ├── ingest-worker/
│   │   ├── pyproject.toml                  # name = svc-ingest
│   │   ├── Dockerfile
│   │   └── ingest_worker/                  # ★ 应用内命名空间包
│   │       ├── api/service.py              # FastAPI 应用入口
│   │       ├── graph/                      # LangGraph：main / state / exceptions / nodes
│   │       └── config.py
│   ├── query-worker/
│   │   ├── pyproject.toml                  # name = svc-query
│   │   ├── Dockerfile
│   │   └── query_worker/                   # ★ 应用内命名空间包
│   │       ├── api/service.py
│   │       ├── graph/                      # main_v1 / main_v2 / state / nodes
│   │       └── config.py
│   ├── eval-runner/                        # 离线评测（待适配）
│   └── apis/                               # 平台侧公共 API（暂不处理）
│
├── packages/                               # 分层共享库（workspace 成员）
│   ├── common/                             # aipaas-common：配置 / 日志 / 上下文 / 通用工具
│   │   └── aipaas_common/
│   │       ├── conf/                       # 中间件与模型配置（dataclass）
│   │       ├── core/                       # log / context / load_prompt
│   │       └── utils/                      # sse / task / path / table / rate_limit
│   ├── llm/                                # aipaas-llm：LLM / 嵌入 / 重排 / MCP
│   ├── retrieval/                          # aipaas-retrieval：Milvus / ES / Qdrant / Neo4j
│   ├── storage/                            # aipaas-storage：MongoDB / MySQL / MinIO / K8s
│   └── pipeline/                           # aipaas-pipeline：RAG 编排基类
│
├── configs/                                # 配置与提示词
│   ├── aipaas_config.yaml
│   ├── admin.conf
│   ├── prompts/rag/*.prompt                # 提示词模板（版本化）
│   └── pipelines/                          # 声明式 pipeline 定义（占位，待建设）
├── data/                                   # 本地样例数据（gitignore）
├── logs/                                   # 运行日志（gitignore）
└── tests/                                  # 全仓验证脚本（按序号/场景组织，非 pytest 用例）
```

## 分层与依赖约定

```
apps/*  ──►  packages/*
                  ▲
        ┌─────────┼─────────┬──────────┐
   pipeline     retrieval   storage     llm
        └─────────┴─────────┴──────────┘
                  │
                common
```

- **单向依赖**：`apps/* → packages/*`，`packages/*` 禁止反向依赖 `apps/*`
- `packages/common` 是最底层，不依赖其他共享包
- `packages/llm` 只依赖 `common`；`retrieval` 依赖 `common` + `llm`；`storage` / `pipeline` 只依赖 `common`
- 应用内一律使用命名空间包（`ingest_worker.*` / `query_worker.*`），**禁止**使用 `graph.*`、`api.*` 这类顶层名——两个应用共存时会发生包名遮蔽
- 所有第三方依赖在**成员** `pyproject.toml` 声明；根 `pyproject.toml` 不声明依赖
- 服务型应用声明 `[tool.hatch.build.targets.wheel] packages = ["ingest_worker"]`，保证 editable 安装后模块名全局唯一

## 统一虚拟环境

所有成员共用 `pymodules/.venv`，不要在成员目录里单独 `uv venv`。

```bash
cd pymodules
uv sync --all-packages        # 必须带 --all-packages，裸 uv sync 只同步根项目
```

`--all-packages` 会把 `apps/ingest-worker`、`apps/query-worker` 及各 `packages/*` 的依赖统一解析进同一环境。

## 快速开始

```bash
cd pymodules
uv sync --all-packages

# 查询服务 http://127.0.0.1:8001
uv run uvicorn query_worker.api.service:app --port 8001 --reload

# 导入服务 http://127.0.0.1:8000
uv run uvicorn ingest_worker.api.service:app --port 8000 --reload
```

容器构建（在 `pymodules` 目录执行）：

```bash
docker build -f apps/query-worker/Dockerfile  -t svc-query  .
docker build -f apps/ingest-worker/Dockerfile -t svc-ingest .
```

## 目标规划（未全部落地）

参考企业级 RAG 目录规划，以下能力**尚未建设**，当前不做空壳占位：

| 规划项 | 状态 | 说明 |
| --- | --- | --- |
| `packages/core` | 未建 | 独立领域模型 / Protocol / ABC；当前基础能力在 `packages/common/core` |
| `packages/ingestion` | 未建 | parsers / cleaners / chunkers；当前解析逻辑仍在 `ingest_worker/graph` |
| `packages/embeddings` | 未建 | 嵌入适配独立包；当前在 `packages/llm` |
| `packages/observability` | 未建 | OTel / token 计量；当前仅有 `common/core/log` |
| `packages/eval` | 未建 | 指标 / 数据集 / LLM-as-judge |
| `packages/platform-sdk` | 未建 | 回调 Java 平台的 client（依赖 contracts/openapi） |
| `apps/embedding-service` | 未建 | 嵌入模型独立 GPU 服务 |
| `apps/reranker-service` | 未建 | 重排模型独立 GPU 服务 |
| `configs/pipelines` | 占位 | 已建目录，声明式 pipeline 定义待补 |
| `apps/apis` | 保留 | 平台侧公共 API，按约定暂不处理 |

拆分原则：**业务编排代码先留在 apps，等能力稳定后再上移到 packages**，避免为目录规划产生空包与假边界。

## 配置说明

- 全局配置：`configs/aipaas_config.yaml`
- 提示词：`configs/prompts/rag/*.prompt`，由 `aipaas_common.core.load_prompt` 按相对路径加载
- 服务级配置：`apps/*/*/config.py`（`python-dotenv` + `os.getenv`）
- 中间件地址（Milvus / MongoDB / MinIO / ES / Nacos 等）：可复用 `deploy/scripts/setup-env.sh` 导出的环境变量

常用环境变量（均有默认值，按需覆盖）：

| 变量 | 说明 |
| --- | --- |
| `QUERY_HIGH_LIKE` | 查询相似度阈值（高/中阈值当前共用该变量） |
| `RERANK_MAX_TOPK` / `RERANK_MIN_TOPK` | 重排后最大/最小保留条数 |
| `RERANK_GAP_ABS` / `RERANK_GAP_RATIO` | 断崖式截断阈值（绝对/相对） |
| `MAX_CONTEXT_CHARS` | 送入模型的最大上下文字符数 |

## 接口一览

**查询服务（8001）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/query` | 查询入口，`is_stream` 控制是否流式 |
| GET | `/stream/{session_id}` | SSE 流式返回 |
| GET | `/history/{session_id}` | 查询会话历史 |
| DELETE | `/history/{session_id}` | 清空会话历史 |
| GET | `/health` | 健康检查 |

**导入服务（8000）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/upload` | 多文件批量上传，自动触发导入流程 |
| GET | `/status/{task_id}` | 任务进度查询（前端轮询） |

## 测试脚本

`tests/` 下为按场景组织的验证脚本，直接以脚本方式运行：

```bash
uv run python tests/01-llm-test.py
uv run python tests/08-sse-test.py
```

## 与前端对接

- `/query-api/**` → `http://127.0.0.1:8001/**`
- `/import-api/**` → `http://127.0.0.1:8000/**`

详见 [web/README.md](../web/README.md)。

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
