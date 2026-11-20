# AIPaaS 文档索引

> 版本基线：五级 Compose 编排已实现，Java 已拆分为 platform-gateway + platform-management，前端默认入口为 HTTPS 443。
> 契约版本：`1.1.0-p0`（`aipaas-contracts/openapi/aipaas-openapi-v1.yaml`）
> 架构总览见 [系统架构说明](02_架构设计/01_系统架构说明.md)。

---

## 命名约定

- **目录**：`序号_分类目录`，例如 `02_架构设计`
- **文档**：`序号_文档名称.md`，例如 `01_系统架构说明.md`
- **数据文件**（json / jsonl / xlsx / 样例原文）保持原名，由验收与解析脚本自动产出，不手工编号。

## 目录结构

```
docs/
├── README.md                  # 本索引
├── 01_入门指南/               # 使用入门、产品规划、环境搭建、模块开发部署
├── 02_架构设计/               # 系统/应用/数据/部署/技术架构、调用链路、P0 契约文档、架构图解 PPT
├── 03_运维验收/               # P0 验收、部署手册、缺陷与上线、各类验收证据
├── 04_决策记录/               # ADR 架构决策记录
├── 05_AI编程/                 # AI 辅助编程提示词
├── 06_解析链路/               # 解析验收报告与样例原文
└── 07_归档/                   # 历史资料（不删减，仅归档）
```

---

## 01_入门指南

| 文档 | 简述 |
| --- | --- |
| [01_快速使用入门.md](01_入门指南/01_快速使用入门.md) | 端到端使用入门（登录、导入、问答、Mock、P0 Compose） |
| [02_产品规划.md](01_入门指南/02_产品规划.md) | 功能规划与路线图 |
| [03_环境搭建.md](01_入门指南/03_环境搭建.md) | Python/Java/Web 开发环境、uv workspace、GPU/CPU 分组 |
| [04_总体部署手册.md](01_入门指南/04_总体部署手册.md) | 从源码到容器的完整部署手册 |
| [05_web模块开发部署.md](01_入门指南/05_web模块开发部署.md) | React + Vite 前端本地开发与构建 |
| [06_python模块开发部署.md](01_入门指南/06_python模块开发部署.md) | Python workspace（uv）本地开发 |
| [07_java模块开发部署.md](01_入门指南/07_java模块开发部署.md) | Java Maven 多模块本地开发 |
| [08_RAG伪代码.md](01_入门指南/08_RAG伪代码.md) | RAG 流程伪代码 |
| [09_RAG评估与优化.md](01_入门指南/09_RAG评估与优化.md) | RAG 评估与优化实践 |
| [10_模块说明文档.md](01_入门指南/10_模块说明文档.md) | Java + Python 双栈模块清单、职责边界与启动方法 |
| [11_待办事项.md](01_入门指南/11_待办事项.md) | 待办事项 |
| [12_风险问题.md](01_入门指南/12_风险问题.md) | 风险与问题记录 |
| [13_实用命令.txt](01_入门指南/13_实用命令.txt) | 常用命令速查 |

## 02_架构设计

| 文档 | 简述 |
| --- | --- |
| [01_系统架构说明.md](02_架构设计/01_系统架构说明.md) | 架构目标、逻辑图、控制面 vs 能力面职责划分、P0/P1+ 边界 |
| [02_应用架构.md](02_架构设计/02_应用架构.md) | 应用清单、端口、单向依赖 Mermaid 图与模块状态矩阵 |
| [03_数据架构.md](02_架构设计/03_数据架构.md) | 数据架构（PostgreSQL / MinIO / Milvus / Redis / MongoDB）、数据流向图 |
| [04_部署架构.md](02_架构设计/04_部署架构.md) | 五级 Compose 编排详解、端口总表、启动依赖链、拓扑图 |
| [05_技术架构.md](02_架构设计/05_技术架构.md) | 技术栈选型、约束、选型对比与不使用清单 |
| [06_调用链路.md](02_架构设计/06_调用链路.md) | 关键链路（登录、上传、查询、管理）Mermaid sequenceDiagram、跨服务接口表 |
| [07_P0领域模型.md](02_架构设计/07_P0领域模型.md) | P0 领域模型（实体、字段、枚举、Mermaid classDiagram） |
| [08_P0_API契约评审.md](02_架构设计/08_P0_API契约评审.md) | P0 OpenAPI 契约评审记录 |
| [09_P0骨架运行手册.md](02_架构设计/09_P0骨架运行手册.md) | Step 0 历史骨架状态（当前端口与集成边界以系统架构说明为准） |
| [10_架构图解.pptx](02_架构设计/10_架构图解.pptx) | 17 页架构图解演示：四层分层、应用清单与依赖、六类存储与数据流、五级编排与拓扑、技术栈与选型、四条调用链路、八条硬约束 |

## 03_运维验收

| 文档 | 简述 |
| --- | --- |
| [01_P0验收报告.md](03_运维验收/01_P0验收报告.md) | P0 集成验收报告（44 项功能、19 项非功能指标） |
| [02_P0部署手册.md](03_运维验收/02_P0部署手册.md) | P0 上线部署手册（配置、端口、TLS、监控、备份、回滚） |
| [03_缺陷清单.md](03_运维验收/03_缺陷清单.md) | 已知缺陷列表 |
| [04_上线检查单.md](03_运维验收/04_上线检查单.md) | 上线前检查清单 |
| [05_端到端测试/01_E2E测试报告.md](03_运维验收/05_端到端测试/01_E2E测试报告.md) | E2E 自动化测试报告（含 E2E-RESULT.json 与条款导出证据） |
| [06_性能压测/01_压测报告.md](03_运维验收/06_性能压测/01_压测报告.md) | 性能压测报告（含 load_test_snapshot.json） |
| [07_质量评估/01_Golden评估报告.md](03_运维验收/07_质量评估/01_Golden评估报告.md) | Golden Set 质量评估报告（含逐条评估明细） |
| [08_安全审计/01_安全报告.md](03_运维验收/08_安全审计/01_安全报告.md) | 安全验收报告（含 security_snapshot.json） |
| `09_黄金数据集/` | golden_set.jsonl 与 corpus.jsonl（由 `pymodules/scripts/acceptance/build_golden.py` 生成） |

> 验收证据目录（`05_端到端测试/E2E-RESULT.json` 等 json/jsonl）为脚本自动产出的历史快照，路径记录以生成时为准；重新运行 `pymodules/scripts/acceptance/` 下脚本即可按新目录结构再生成。

## 04_决策记录

| 文档 | 简述 |
| --- | --- |
| [01_功能清单审查决策记录.md](04_决策记录/01_功能清单审查决策记录.md) | 功能清单审查的架构决策记录（ADR） |

## 05_AI编程

| 文档 | 简述 |
| --- | --- |
| [01_Java网关与管理拆分实施提示词.md](05_AI编程/01_Java网关与管理拆分实施提示词.md) | Java 侧网关/管理拆分的实施提示词 |

## 06_解析链路

| 文档 | 简述 |
| --- | --- |
| [01_解析链路报告.md](06_解析链路/01_解析链路报告.md) | 解析质量验收报告（PDF/Word/Excel/Markdown） |
| `02_解析样例/` | 样例原文（pdf/word/excel/markdown 子目录，由 `pymodules/scripts/run_parse_report.py` 生成） |

## 07_归档

| 目录 | 简述 |
| --- | --- |
| `01_AI设计产物/` | 架构图 HTML、产品规划 Excel、功能规划提示词 |
| `02_学习笔记/` | 早期技术笔记（技术架构、SSE、Rerank、多路检索、Milvus 索引、文档切片等）、项目部署笔记、流程图图片 |
| `03_手绘文档/` | 手绘 RAG 分层架构文档与 PPT |

> 归档内容为历史资料，保持原样不删减；其中引用的本地图片与外部路径可能已失效，不影响主文档。

---

## 版本与约束

### 已实现、已落地

| 项 | 事实源 |
| --- | --- |
| 五级 Compose 编排（base → models → python → java → frontend） | `deploy/docker/docker-compose.yaml` / `.env.example` |
| Java 已拆分为 platform-gateway + platform-management | `javaplat/pom.xml` |
| gateway multipart.enabled=false 流式转发 | `javaplat/apps/platform-gateway/src/main/resources/application.yml` |
| query 链路直达 svc-query（不经过 management） | `javaplat/apps/platform-gateway/.../ChatController.java` |
| PostgreSQL Flyway V1~V9 迁移 | `javaplat/apps/platform-management/src/main/resources/db/migration/` |
| SSE 流式问答（event: delta/citation/done） | `pymodules/apps/query-worker/query_worker/api/routers/stream.py` |
| Milvus HNSW + BGE-M3 | `pymodules/packages/pipeline/vector_index.py` |
| MinIO 对象存储 + AES-GCM 加密 | `deploy/docker/docker-compose-java.yaml` |
| Redis 会话与 JTI 撤销 | `javaplat/apps/platform-management/.../application.yml` |
| Prometheus / Grafana 可观测 | `deploy/docker/docker-compose-p0.yaml`（profile: observability） |
| 前端 HTTPS 443 默认入口 | `web/nginx.conf.template` + `.env.example` |
| 统一响应信封 {code, message, data, traceId} | `aipaas-contracts/README.md` |
| 幂等键（Idempotency-Key，24h） | `aipaas-contracts/README.md` |
| 契约校验 + 可运行 mock | `aipaas-contracts/mock/server.py` |
| SSO MFA / 多租户（tenant_id 字段在表结构内但无管理界面） | V1__security_baseline.sql |

### 规划中、骨架中

| 项 | 说明 |
| --- | --- |
| rag-ai-api 的 501 契约占位路由 | `pymodules/apps/rag-ai-api/rag_ai_api/main.py` — 除 /healthz、/readyz、/api/v1/system/info 外统一返回 501 + SYS.NOT_IMPLEMENTED |
| svc-model-reranker 的开发全量链路 | 仅在 docker-compose-models.yaml 中启动，P0 chat 链路未直接调用 |
| BM25 关键词召回 | P1 规划 |
| Cross-Encoder 重排 | P1 规划 |
| 分布式全局限流 | Resilience4j 只做单实例限流（p0Api） |
| 服务网格 / K8s | 当前部署基线为 Compose |
| RocketMQ | profile: rocketmq，默认不启动 |
| Nacos 注册中心 | profile: nacos，默认不启动 |
| vLLM 本地推理 | profile: vllm，需 GPU |
| 多租户管理界面 | 表结构已包含 tenant_id 但无管理端点 |
| 分布式追踪系统 | TraceId 贯通链路，但依赖 Prometheus/本地 Span，未接 OpenTelemetry Collector |
| 完整 SSO / MFA | 表结构预留 sec_refresh_tokens，但 OAuth/OIDC 未接入 |

---

## 事实源清单

以下目录/文件为仓库的权威事实源；本文档所有内容均以此为准。**不要**在这些目录之外重复维护副本。

| 事实源 | 内容 |
| --- | --- |
| `pymodules/pyproject.toml` | Python workspace 根（uv workspace 声明、cpu/gpu 分组） |
| `pymodules/apps/*/pyproject.toml` | 各 Python 应用依赖与入口 |
| `pymodules/packages/*/pyproject.toml` | 各 Python 共享库 |
| `javaplat/pom.xml` | Java Maven 多模块根（libs/common、apps/gateway、apps/management） |
| `javaplat/apps/*/src/main/resources/application.yml` | Java 服务运行时配置 |
| `javaplat/apps/platform-management/src/main/resources/db/migration/` | Flyway V1~V9 迁移（PostgreSQL schema 权威） |
| `deploy/docker/docker-compose.yaml` | 五级编排顶层（frontend 第 5 级） |
| `deploy/docker/docker-compose-java.yaml` | Java 编排（第 4 级） |
| `deploy/docker/docker-compose-python.yaml` | Python 编排（第 3 级） |
| `deploy/docker/docker-compose-models.yaml` | 模型编排（第 2 级） |
| `deploy/docker/docker-compose-base.yaml` | 基础设施编排（第 1 级） |
| `deploy/docker/docker-compose-p0.yaml` | P0 独立编排（避开宿主机冲突端口） |
| `deploy/docker/.env.example` | 部署环境变量模板（COMPOSE_PROFILES、端口、密钥） |
| `aipaas-contracts/openapi/aipaas-openapi-v1.yaml` | OpenAPI 3.1 契约（冻结版 1.1.0-p0） |
| `web/nginx.conf.template` | 前端反代规则（/api/** → gateway、/query-api/ → svc-query、/import-api/ → svc-ingest） |
| `pymodules/configs/aipaas_config.*.yaml` | Python 业务侧模型阈值、连接地址（配置包） |

---

## 维护约定

- **禁止全量覆盖已有文件**；全部采用增量/新建文件方式。
- 新增文档按 `序号_文档名称.md` 命名，放入对应 `序号_分类目录`，并更新本索引。
- 新增架构文档请放在 `02_架构设计/` 下；新增验收证据由验收脚本自动产出，不手工编号。
- 若 `aipaas-contracts` 契约有破坏性变更，先跑 `aipaas-contracts/scripts/verify_contract.py`，再更新本文档。
- 部署相关变更同步到 `.env.example` 与本索引"事实源清单"。
- 文档目录重命名时，必须同步检查 `pymodules/scripts/` 下的硬编码路径（`harness.py`、`e2e_p0.py`、`run_parse_report.py` 等）。
