# AIPaaS

面向金融标准化知识库的检索增强生成（RAG）平台，提供文档解析、条款级分块、向量索引、标准号/条款号定位、引用问答、无证据拒答、标准简介、条目化导出、权限审计和可观测能力。

仓库采用多语言 Monorepo：Java 负责平台控制面，Python 负责 AI 能力，React 负责交互界面，OpenAPI 负责跨端契约。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [快速使用入门](docs/00_快速使用入门.md) | Mock 演示、P0 Compose、离线验收和开发全量环境的最短路径 |
| [模块说明文档](docs/08_模块说明文档.md) | Java + Python 双栈的模块清单、职责边界，以及**每个模块的启动方法** |
| [架构说明](docs/03_架构说明.md) | 实际模块、调用链、数据存储、安全、部署与 P0/P1+ 边界 |
| [P0 部署手册](docs/acceptance/P0-DEPLOYMENT-MANUAL.md) | 配置、端口、TLS、监控、备份、升级和回滚 |
| [P0 验收报告](docs/acceptance/P0-ACCEPTANCE-REPORT.md) | 44 项功能、19 项非功能指标及端到端证据 |
| [接口契约](aipaas-contracts/README.md) | OpenAPI、Mock、版本兼容与契约变更流程 |
| [Python 工程](pymodules/README.md) | uv workspace、业务服务、共享包和模型配置 |
| [贡献指南](CONTRIBUTING.md) | 分支、开发、测试、提交和 Pull Request 规范 |
| [行为准则](CODE_OF_CONDUCT.md) | 社区协作、评审和违规处理约定 |
| [安全政策](SECURITY.md) | 漏洞私密报告与敏感信息处理 |

## 当前实现一览

| 模块 | 技术栈 | 当前职责 |
| --- | --- | --- |
| `javaplat/apps/platform-management` | Java 21 / Spring Boot 3 / Maven | 管理服务：登录/刷新/注销、用户与 RBAC、知识库、文档、审计落库、内容策略、系统配置、导入任务和数据库、Flyway、MinIO、完整密钥 |
| `javaplat/apps/platform-gateway` | Java 21 / Spring Boot 3 / Maven | 统一入口网关：本地 JWT 验签与 JTI 撤销校验、身份头重建、`/api/v1/**` 流式代理、问答安全编排、审计事件发布、Trace/幂等/SSE |
| `pymodules/apps/ingest-worker` | Python 3.12 / FastAPI | 文档解析、清洗、分块、元数据、Embedding 和标准简介；默认 8001 |
| `pymodules/apps/query-worker` | Python 3.12 / FastAPI | 向量检索、权限过滤、生成、引用、拒答和 SSE；默认 8002 |
| `pymodules/apps/model-embedding-worker` | Python 3.12 / torch | BGE-M3（或在线 Embedding）独立推理，dense + sparse；默认 8003 |
| `pymodules/apps/model-reranker-worker` | Python 3.12 / torch | bge-reranker-large（或 DashScope TextReRank）独立推理；默认 8004 |
| `pymodules/apps/rag-ai-api` | FastAPI | P0 容器契约骨架；健康检查与系统信息，业务路由返回 501 |
| `pymodules/packages` | uv workspace | `pipeline`、`retrieval`、`generation` 等可复用 AI 业务包 |
| `web` | React 18 / Vite / Nginx | 对话、导入、知识库、导出和个人中心；登录页当前仍为演示模式 |
| `aipaas-contracts` | OpenAPI 3.1 | P0 契约唯一权威来源，附零依赖 Mock 和校验脚本 |
| `deploy/docker` | Docker Compose | P0 交付骨架与开发全量两套编排，含 Prometheus/Grafana/Alertmanager |
| `docs` | Markdown | 架构、部署、解析、质量、安全、验收和上线材料 |

> 状态说明：P0 已有可复现验收材料，但 P0 Compose 中的 `rag-ai-api` 仍是骨架，真实 AI 能力位于 `ingest-worker`、`query-worker` 和共享包。不要把“契约冻结”“容器健康”或“离线 Stub 验收”理解为真实模型生产链路已经全部接通，详见[架构说明的集成边界](docs/03_架构说明.md#81-p0-compose契约与平台交付骨架)。

## 快速开始

### 1. 最短的界面与契约演示

在仓库根目录启动 Mock：

```bash
python3 aipaas-contracts/mock/server.py --port 4010
```

另开终端启动前端：

```bash
cd web
npm ci
PLATFORM_API_TARGET=http://127.0.0.1:4010 npm run dev
```

访问 `http://localhost:5173`，使用任意非空账号密码进入演示界面。当前演示登录尚未与 P0 Bearer Token 打通，浏览器内 API 调用不能据此视为已认证；请独立运行契约冒烟验证上传→问答链路：

```bash
python3 aipaas-contracts/mock/smoke_upload_qa.py \
  --base-url http://127.0.0.1:4010
```

### 2. 启动 P0 容器环境

```bash
cd deploy/docker
cp .env.example .env
# 编辑 .env，至少设置 P0_BOOTSTRAP_ADMIN_PASSWORD、P0_PG_PASSWORD、
# P0_MINIO_PASSWORD、GRAFANA_ADMIN_PASSWORD

docker compose -f docker-compose-p0.yaml config -q
docker compose -f docker-compose-p0.yaml up -d --build
docker compose -f docker-compose-p0.yaml ps
```

Web：`http://localhost:18080`；Grafana：`http://localhost:13000`；Prometheus：`http://localhost:19090`。平台 API 默认仅在容器内网暴露，完整健康检查和边界见[快速入门](docs/00_快速使用入门.md#3-b-p0-compose-部署)。

### 3. 复现真实 Python 业务包验收

```bash
cd pymodules
uv sync --all-packages --group cpu
uv run python scripts/acceptance/e2e_p0.py
uv run python scripts/acceptance/golden_eval.py
uv run pytest tests -q
```

该路径复用生产解析、检索和生成代码，并使用明确标注的确定性替身；证据输出到 `docs/acceptance/`。

## 仓库结构

```text
aipaas/
├── aipaas-contracts/          # OpenAPI、Mock、契约校验与版本策略
├── javaplat/                  # Java 21 Maven 多模块：platform-gateway + platform-management + common
├── pymodules/                 # Python uv workspace：apps + packages + tests
│   ├── apps/                  # ingest/query/model/rag-ai-api
│   ├── packages/              # pipeline/retrieval/generation/common 等
│   └── scripts/acceptance/    # 可复现 P0 验收脚本
├── web/                       # React + Vite 前端与 Nginx 镜像
├── deploy/                    # Docker Compose、监控、环境与初始化脚本
├── docs/                      # 项目文档与验收证据
├── .github/                   # Issue 与 Pull Request 模板
├── CONTRIBUTING.md
└── SECURITY.md
```

## 本地开发与验证

| 范围 | 命令 |
| --- | --- |
| 各模块职责与启动 | 见[模块说明文档](docs/08_模块说明文档.md) |
| 契约校验 | `python3 aipaas-contracts/scripts/verify_contract.py` |
| Java 测试 | `cd javaplat && ./mvnw clean test`（管理 91 + 网关 18，共 109 个测试） |
| Java 本地启动 | `cd javaplat && ./mvnw -pl apps/platform-gateway -am spring-boot:run`（容器内端口 8080，宿主机默认 8081）、`./mvnw -pl apps/platform-management -am spring-boot:run`（8080，需可连 PostgreSQL 与 Redis） |
| Python 环境 | `cd pymodules && uv sync --all-packages --group cpu` |
| Python 测试 | `cd pymodules && uv run pytest tests -q` |
| 导入服务 | `cd pymodules && uv run python -m ingest_worker.api.app` |
| 查询服务 | 另一个终端执行 `cd pymodules && uv run python -m query_worker.api.app` |
| 嵌入 / 重排服务 | `uv run python -m model_embedding_worker.api.app` / `uv run python -m model_reranker_worker.api.app` |
| Web 开发 | `cd web && npm ci && npm run dev` |
| Web 构建 | `cd web && npm run build` |

两套 Compose 不混用：

- `deploy/docker/docker-compose-p0.yaml`：P0 平台、契约骨架、基础设施和监控；
- `deploy/docker/docker-compose.yaml`：开发全量 Python/模型服务及既有中间件。

开发全量链路的混合检索、重排、联网搜索等能力不属于 P0 金融标准契约的统一验收口径。

## 参与贡献

1. 阅读[贡献指南](CONTRIBUTING.md)和[架构说明](docs/03_架构说明.md)。
2. 从最新默认分支创建 `feat/<scope>-<summary>`、`fix/<scope>-<summary>` 或 `docs/<scope>-<summary>` 分支。
3. 按变更范围运行契约、Java、Python、Web 和端到端验证。
4. 使用 Issue/PR 模板提交，说明契约、权限、审计、部署和文档影响。
5. 不在 Issue、日志或提交中粘贴口令、Token、私钥和敏感业务数据；漏洞按[安全政策](SECURITY.md)私密报告。
