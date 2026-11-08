# pymodules（Python Monorepo）

AIPaaS 的 Python AI 服务工程，采用 **uv workspace monorepo** 管理：多个可独立部署的业务应用共享同一套虚拟环境、同一份锁文件与同一个公共库。

## 服务一览

| 服务 | 目录 | 端口 | 职责 |
| --- | --- | --- | --- |
| 文档导入 | `apps/ingest` | 8000 | 文件上传、解析、切分、向量化、入库（LangGraph 编排） |
| 知识库查询 | `apps/query` | 8001 | 检索、重排、答案生成、SSE 流式输出 |

## 目录结构

```
pymodules/                        # Python monorepo（uv workspace 根）
├── pyproject.toml                # workspace 根：members = apps/ingest、apps/query、libs/common
├── uv.lock                       # 全仓统一锁文件
├── .python-version               # Python 3.12
│
├── apps/                         # 可部署业务应用（workspace 成员，只依赖 libs）
│   ├── ingest/                   # 文档导入服务 → :8000
│   │   ├── pyproject.toml        # name = svc-ingest
│   │   ├── config.py             # 导入流程配置
│   │   ├── api/service.py        # FastAPI 应用 + uvicorn 入口
│   │   └── graph/                # LangGraph 编排：main.py / state.py / exceptions.py
│   └── query/                    # 用户查询服务 → :8001
│       ├── pyproject.toml        # name = svc-query
│       ├── config.py             # 检索/重排阈值等配置
│       ├── api/service.py        # FastAPI 应用 + uvicorn 入口
│       └── graph/                # 查询编排：main_v1.py / main_v2.py / state.py
│
├── libs/                         # 共享库（workspace 成员）
│   └── common/aipaas_common/     # 包名 aipaas-common
│       ├── clients/              # milvus / mongo / es / minio / neo4j / qdrant / llm / mcp / k8s 客户端
│       ├── conf/                 # 中间件与模型配置（dataclass）
│       ├── core/                 # log / context / load_prompt
│       └── utils/                # sse / task / path / table / rerank / rate_limit
│
├── conf/                         # 全局配置：aipaas_config.yaml、admin.conf
├── prompts/                      # 提示词模板：rag/ 及各 .prompt 文件
├── tests/                        # 全仓验证脚本（按序号组织，非 pytest 用例）
└── app/                          # 平台侧 FastAPI（apis/routers、apis/svc），当前未纳入 workspace 成员
```

## 分层与依赖约定

- `apps/*`：可独立启动的业务服务，只允许**单向依赖** `libs/*`
- `libs/common`：全仓唯一共享库 `aipaas-common`，禁止反向依赖 `apps/*`
- 所有第三方依赖在**成员** `pyproject.toml` 中声明，根 `pyproject.toml` 只做 workspace 声明，不装依赖
- 新增服务：创建 `apps/xxx`（含独立 `pyproject.toml`），并在根 `members` 中注册
- `app/` 为平台侧接口（容器/镜像/流水线等），未纳入 workspace，后续计划单独重构迁移

## 环境要求

- Python 3.12（见 `.python-version`）
- [uv](https://docs.astral.sh/uv/)（依赖管理与虚拟环境）

## 快速开始

```bash
cd pymodules
uv sync                                          # 安装 workspace 全部成员依赖

# 查询服务 http://127.0.0.1:8001
uv run python apps/query/api/service.py

# 导入服务 http://127.0.0.1:8000
uv run python apps/ingest/api/service.py
```

`uv sync` 会按 editable 模式安装 `apps/ingest`、`apps/query`、`libs/common`，因此 `graph`、`api`、`aipaas_common` 等模块可直接导入。

## 配置说明

- 全局配置：`conf/aipaas_config.yaml`
- 服务级配置：`apps/*/config.py`（通过 `os.getenv` + `python-dotenv` 读取 `.env`）
- 中间件地址（Milvus / MongoDB / MinIO / ES / Nacos 等）：可复用 `deploy/scripts/setup-env.sh` 导出的环境变量
- 提示词模板：`prompts/`，由 `aipaas_common.core.load_prompt` 加载

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
| GET | `/chat.html` | 旧版对话页面 |

**导入服务（8000）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/upload` | 多文件批量上传，自动触发导入流程 |
| GET | `/status/{task_id}` | 任务进度查询（前端轮询） |
| GET | `/import.html` | 旧版导入页面 |

## 测试脚本

`tests/` 下为按序号组织的验证脚本，直接以脚本方式运行，例如：

```bash
uv run python tests/01-llm-test.py
uv run python tests/08-sse-test.py
```

## 与前端对接

前端通过前缀反代到两个服务（开发期 Vite 代理，生产期 nginx）：

- `/query-api/**` → `http://127.0.0.1:8001/**`
- `/import-api/**` → `http://127.0.0.1:8000/**`

详见 [web/README.md](../web/README.md)。
