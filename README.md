# aipaas

人工智能平台即服务（AI PaaS）：面向金融标准化的知识库问答平台，由 Python AI 服务、Java 微服务平台、React 前端与部署编排组成。

## 软件架构

| 模块                               | 技术栈                                              | 说明                                                   |
| -------------------------------- | ------------------------------------------------ | ---------------------------------------------------- |
| [pymodules](pymodules/README.md) | Python 3.12 / uv workspace / FastAPI / LangGraph | 文档导入（:8000）与知识库查询（:8001）AI 服务                        |
| [web](web/README.md)             | React 18 / Vite / nginx                          | 对话、文件导入、个人中心前端                                       |
| [javaplat](javaplat)             | Java 服务构建目录                                      | gateway / svc-document / svc-search（当前仅含 Dockerfile） |
| [deploy](deploy)                 | Docker Compose / K8s                             | 中间件（Milvus、etcd、MinIO、MongoDB）、Nacos、离线包与初始化脚本       |
| [docs](docs)                     | Markdown                                         | 设计文档与待办事项                                            |
| [eval](eval)                     | Python                                           | 检索与问答效果评测脚本                                          |

Python 侧 `pymodules` 采用 **uv workspace monorepo** 结构：`apps/` 存放可独立部署的业务服务，`libs/` 存放共享库，依赖单向流动（apps → libs）。详见 [pymodules/README.md](pymodules/README.md)。

## 安装教程

### 1. 启动中间件与依赖服务

```bash
cd deploy
source scripts/setup-env.sh      # 导出中间件地址、JAVA_HOME、DOCKER_VOLUME_DIRECTORY 等
docker-compose -f docker-compose.yaml up -d
```

`docker-compose.yaml` 内置 `init-volumes` 一次性容器，会自动创建数据目录并把属主对齐到当前执行用户，无需手工 `chown`。

### 2. 启动 Python 服务

```bash
cd pymodules
uv sync --all-packages
uv run uvicorn query_worker.api.app:app --port 8001      # 查询服务 http://127.0.0.1:8001
uv run uvicorn ingest_worker.api.app:app --port 8000     # 导入服务 http://127.0.0.1:8000
```

### 3. 启动前端

```bash
cd web
npm install && npm run dev                   # 开发模式 http://localhost:5173
```

生产构建与容器运行方式见 [web/README.md](web/README.md#docker-构建与运行)。

## monorepo目录结构【待完善】

```
aipaas/
├── contracts/                        # ★ 跨语言接口契约（单一事实来源）【待分析完善】
│   ├── proto/                        # Java ↔ Python 内部 gRPC 契约
│   │   ├── ingestion/v1/*.proto
│   │   ├── retrieval/v1/*.proto
│   │   └── common/v1/*.proto
│   ├── openapi/                      # Java 对外 REST API（平台 API）
│   │   ├── platform-v1.yaml
│   │   └── admin-v1.yaml
│   ├── asyncapi/                     # 异步事件（Kafka/MQ）
│   │   ├── document-ingested.yaml
│   │   └── task-status.yaml
│   ├── schemas/                      # 跨语言共享 DTO（JSON Schema）
│   ├── buf.yaml / buf.gen.yaml       # proto 生成 & 兼容性 lint
│   └── README.md                     # 版本策略 / 兼容规则
│
├── javaplat/                             # ★ 平台业务域（Gradle 多模块）
│   ├── settings.gradle.kts
│   ├── build.gradle.kts
│   ├── gradlew
│   ├── apps/
│   │   ├── platform-api/             # 对外主 API：用户/租户/鉴权/计费/网关
│   │   ├── admin-service/            # 管理后台 BFF
│   │   ├── document-service/         # 文档元数据、配额、审计
│   │   └── ai-orchestrator/          # 调用 Python AI 服务的编排入口（同步+异步）
│   ├── libs/
│   │   ├── common/                   # 工具、错误模型、结果类型
│   │   ├── security/                 # 认证/授权、mTLS、服务令牌
│   │   ├── ai-contract/              # 由 contracts/proto 生成的 Java stub + 封装
│   │   └── persistence/              # JPA/MyBatis 公共配置
│   └── config/                       # checkstyle / spotbugs / jacoco
│
├── pymodules/                        # ★ 算法与流程编排域（uv workspace）
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── apps/
│   │   ├── ai-service/               # 对外 gRPC/HTTP：检索·重排·生成·会话
│   │   │   ├── src/rag_service/
│   │   │   └── pyproject.toml
│   │   ├── ingest-worker/            # 解析→清洗→分块→嵌入→入库
│   │   ├── query-worker/             # 长查询、批处理、异步重排
│   │   ├── eval-runner/              # 离线评测执行器（CI/定时）
│   │   ├── embedding-service/        # 嵌入模型：GPU 池独立部署
│   │   └── reranker-service/         # 重排序模型：GPU 池独立部署
│   ├── packages/
│   │   ├── core/                     # 领域模型 + Protocol/ABC（零三方依赖）
│   │   ├── ingestion/                # parsers / cleaners / chunkers
│   │   ├── embeddings/               # 嵌入适配 + 批处理 + 缓存
│   │   ├── retrieval/                # 向量库 adapter、混合检索、rerank
│   │   ├── llm/                      # LLM 客户端抽象、限流、路由
│   │   ├── pipeline/                 # RAG 流程编排（ingest/query 主线）
│   │   ├── eval/                     # 指标、数据集、LLM-as-judge
│   │   ├── observability/            # 结构化日志、OTel、token 计量
│   │   ├── common/                   # 配置、DI、重试、缓存
│   │   └── platform-sdk/             # ★ 回调 Java 平台的 client（由 openapi 生成）
│   ├── configs/
│   │   ├── pipelines/                # 声明式 pipeline 定义
│   │   └── prompts/                  # prompt 模板（版本化）
│   └── data/                         # 本地样例（gitignore）
│
├── web/                              # 前端（与语言域解耦）
│   ├── admin-web/                    # Next.js 管理后台
│   ├── chat-web/                     # Next.js 用户端
│   ├── pnpm-workspace.yaml
│   └── package.json
│
├── sdks/                             # 对外发布 SDK（由 contracts 生成）
│   ├── java/                         # Maven Central
│   ├── python/                       # PyPI
│   └── ts/                           # npm
│
├── deploy/                           # 部署与运行基础设施
│   ├── docker/                       # 各 app Dockerfile、compose.dev.yaml
│   ├── k8s/                          # manifests / kustomize
│   ├── helm/                         # 按服务分包
│   ├── terraform/                    # 向量库、对象存储、MQ、RDS
│   └── migrations/                   # 关系库迁移（Flyway，Java 侧主导）
│
├── configs/                          # 跨域共享配置（极少）
│   ├── feature-flags.yaml
│   └── models.yaml                   # 共享的模型清单（两边都读）
│
├── scripts/                          # 跨语言运维脚本
│   ├── bootstrap.sh
│   ├── gen-contracts.sh              # buf generate + openapi-generator
│   ├── reindex.py                    # 触发全量重建（调 Python CLI）
│   └── smoke_test.sh
│
├── tests/                            # ★ 跨语言测试
│   ├── contract/                     # 契约测试（Java 侧 stub ↔ Python 实现）
│   ├── integration/                  # 起 docker-compose 跑真实向量库/MQ
│   └── e2e/                          # 平台→AI 全链路
│
├── docs/
│   ├── adr/                          # 含「为什么选 gRPC + contracts/ 目录」等决策
│   ├── api/
│   └── runbooks/
│
├── .github/workflows/
│   ├── contracts.yml                 # buf lint / breaking / 生成物 diff
│   ├── java-ci.yml
│   ├── python-ci.yml
│   ├── eval-gate.yml
│   └── e2e.yml                       # 按路径过滤触发
├── Makefile                          # 统一入口，内部委派各语言
├── .env.example
└── README.md
```

## 使用说明

1. 前端登录后进入 `/chat` 进行知识库问答，支持流式（SSE）与非流式返回
2. `/import` 上传文档，自动触发解析、切分、向量化与入库流程
3. 健康检查：查询服务 `GET /health`；中间件端口映射见 [deploy/docker-compose.yaml](deploy/docker-compose.yaml)

## 参与贡献

1. Fork 本仓库
2. 新建 `Feat_xxx` 分支
3. 提交代码
4. 新建 Pull Request

