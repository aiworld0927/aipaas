# P0 接口契约评审记录（Step 0）

> 评审对象：[aipaas-openapi-v1.yaml](../../aipaas-contracts/openapi/aipaas-openapi-v1.yaml) 版本 `1.0.0-p0`（`x-contract-status: frozen`）
> 评审日期：2026-11-17
> 评审依据：《企业级RAG系统功能清单 (3).xlsx》`AIPAAS功能清单` 中 P0 共 44 项 + 用户 Step 0 任务书
> 结论：**通过（契约冻结）**；遗留项见第 8 节，均不阻塞冻结。

---

## 1. 评审范围与方法

| 项 | 内容 |
|---|---|
| 规模 | 61 个 path / 71 个 operation / 167 个 schema / 601 个 `$ref` |
| 自动校验 | ① YAML 语法与重复键 ② `$ref` 完整性（0 坏引用）③ 写接口 `x-permission` + `x-audit` 声明率 ④ 44 项 P0 功能覆盖计数 |
| 人工评审 | 接口粒度、命名、幂等语义、权限粒度、错误码分段、P1/P2 边界 |
| 评审角色 | 架构（接口与边界）、安全（鉴权/审计/越权）、前端（可联调性）、后端（可实现性） |

---

## 2. 10 个功能域契约齐备性

| # | 功能域 | 主要接口 | 覆盖功能 | 结论 |
|---|---|---|---|---|
| 1 | 数据接入与解析 | 上传、文档查询/删除、重解析、解析结果、清洗报告、任务列表/详情/重试/事件流 | #1 #10 #14 #18 #19 #20 #21 #134 #252 | 齐备 |
| 2 | 知识构建与索引 | 元数据读写、分块列表/预览、条款清单、向量化任务（创建/查询/暂停/恢复）、索引构建（创建/查询/重试）、索引状态 | #36 #37 #44 #49 #57 #404 | 齐备 |
| 3 | 检索引擎 | 检索、查询理解 | #63 #70 #88 | 齐备 |
| 4 | 生成与推理 | 问答、流式问答、标准简介、Prompt 模板与版本 | #98 #103 #106 #108 #110 #113 #114 #405 #407 | 齐备 |
| 5 | 知识治理 | 知识库 CRUD、角色/权限、导出（创建/查询/下载） | #130 #139 #406 | 齐备 |
| 6 | 安全与合规 | 登录/登出/当前用户、安全态势、策略读写、安全检查 | #153 #154 #156 #157 #159 #166 | 齐备 |
| 7 | 可观测与质量评估 | 请求日志、Trace 详情、指标汇总 | #173 #175 #177 | 齐备 |
| 8 | 平台与管理 | 用户管理、系统配置、管理概览 | #193 | 齐备 |
| 9 | 用户界面与体验 | 能力选择器、UI 配置 | #242 #246 #402 | 齐备 |
| 10 | 模型服务 | 模型提供商、模型对话、向量化、模型健康 | #98 #103 #44 | 齐备 |

**统计**：44/44 项 P0 功能均在 operation 的 `x-p0-feature` 中显式标注；无 P0 功能缺少接口。

---

## 3. 功能 → 接口 → 验收标准对齐表

| 功能 | 接口 | 验收标准在契约中的落点 |
|---|---|---|
| #1 文档上传 | `POST /knowledge-bases/{kbId}/documents` | `multipart/form-data`；`format` 枚举校验；`413 PayloadTooLarge`；`Idempotency-Key` 必填 |
| #10 任务状态可见/重试 | `GET /ingest/tasks`、`GET /ingest/tasks/{taskId}`、`POST ...:retry`、`GET .../events` | `progress/stage`、`retryCount ≤ maxRetries(2)`、重试接口幂等、SSE 推送 |
| #14 清洗差异可追踪 | `GET /documents/{id}/cleaning-report` | `CleaningReport` 含前后文本与 `CleaningDiff[]` |
| #18/#19/#20/#21 解析产章节树/表格/条款锚点 | `POST /documents/{id}:reparse`、`GET .../parse-result` | `ParseResult{sectionTree, tables, clauseAnchors, failure?}`；失败含错误码，重试上限 2 |
| #36/#37 分块预览与条目化 | `GET .../chunks`、`POST .../chunks:preview` | `ChunkPage` + `ChunkPreviewRequest{strategy,size,overlap}`；`chunkType` 三态 |
| #44 批量向量化可暂停恢复 | `POST /index/embedding-jobs`、`GET`、`:pause`、`:resume` | `JobStatus{PAUSED}`、`EmbeddingFailure[]`；`Idempotency-Key` 必填 |
| #49 HNSW 索引 | `POST /index/builds`、`GET`、`:retry`、`GET /knowledge-bases/{kbId}/index-status` | `IndexState` 五态；P2 预留 `indexType/metric/params/indexSlot/alias` |
| #57 元数据 | `GET/PUT /documents/{id}/metadata` | `StandardMeta` 完整字段 + `source` 区分自动抽取/人工校正 |
| #63 查询归一 | `POST /retrieve/query-understanding` | `QueryUnderstanding{normalizedQuery,keywords,entities,filters}`；只读计算无审计 |
| #70 向量召回 | `POST /retrieve` | `topK`、`RetrieveHit.rank/score`；`retrievalMode` 枚举仅 `vector` |
| #88 权限过滤 0 泄露 | `POST /retrieve` | 服务端裁剪 + `permission.denied` 审计动作 |
| #98 双 LLM 提供商 | `GET /models/providers`、`GET /models/health`、`POST /chat` | `ModelProvider[]`（≥2）+ `ModelStatus`；`chat` 声明审计 `model.invoke` |
| #103 Prompt 版本 | `GET /prompts/templates`、`GET .../{name}/versions` | `PromptTemplate{currentVersion, versions[]}`；`Answer.promptTemplateVersion` |
| #106 拒答 | `POST /chat` | `Answer.refused` + `refusalReason` + `finishReason=REFUSED` |
| #108 输入校验 | `POST /chat`、`POST /chat/stream` | `PARAM.*` 错误码；`security.input.blocked` 审计动作 |
| #110 格式化输出 | `POST /chat` | `AnswerFormat=MARKDOWN`、`content` 描述说明 |
| #113 流式 | `POST /chat/stream` | SSE `meta/delta/citation/done/error/ping`，`seq` 作 `Last-Event-ID` |
| #114 标准问答 | `POST /chat` | `capability=RAG` 默认；引用必填 |
| #130 知识库管理 | `/knowledge-bases` CRUD、`/index-status` | 分页参数、`KnowledgeBaseStatus`、删除为软删（`DELETED`） |
| #134 文档管理 | `/documents` 列表/详情/删除 | `DocumentPage` 过滤 `kbId/status/format`；删除审计 |
| #139 RBAC | `/roles`、`/permissions` | `Role.permissions[]` 引用 `Permission.code`；写接口审计 |
| #144 审计 ≥180 天 | `GET /audit-logs` | `AuditLog` 字段齐全；`RetentionPolicy` 在 `SystemConfig` |
| #153/#154 TLS/存储加密 | `GET /security/posture` | `TlsPosture` + `StorageEncryptionPosture` |
| #156/#157/#159 内容审核/注入/账号 | `GET/PUT /security/policies`、`POST /security/checks`、`/auth/*` | `SecurityCheckType{INPUT_CONTENT,OUTPUT_CONTENT,PROMPT_INJECTION}`；`SecurityAction` |
| #166 登录 | `POST /auth/login`、`POST /auth/logout`、`GET /auth/me` | `TokenPair{accessToken,expiresIn}`；`AUTH.*` 错误码；登录失败审计 |
| #173 请求日志 | `GET /observability/request-logs` | `RequestLogEntry{traceId,path,status,latencyMs,...}` |
| #175 全链路 Trace | `GET /observability/traces/{traceId}` | `TraceDetail{spans[]}`，`traceId` 与响应头一致 |
| #177 QPS/延迟/成功率/资源 | `GET /observability/metrics/summary` | `MetricsSummary{qps,p95LatencyMs,successRate,cpu,memory}` + Prometheus 抓取 |
| #193 管理后台 | `/admin/users`、`/admin/system-config`、`/admin/overview` | 用户状态机、配置读写审计、概览聚合 |
| #242 对话界面 | `GET /ui/config` | `UIConfig` 含入口开关/流式开关 |
| #246 知识库界面 | `GET /ui/config` | 同上，含知识库展示配置 |
| #252 上传/下载/删除权限实时校验 | 上传/删除/导出下载接口 | 每个写接口 `x-permission`；越权返回 `PERM.FORBIDDEN` 并记 `permission.denied` |
| #402 能力选择器（仅 RAG） | `GET /ui/capabilities`、`GET /ui/config` | `Capability[]` 中 `available` 仅 RAG 为 true |
| #404 条款清单 | `GET /documents/{id}/clauses` | `ClausePage`，`Clause.level` 表达层级 |
| #405 标准简介 | `POST /documents/{id}/summaries`、`GET .../latest` | `Summary{elements[], standardMetaSnapshot}` |
| #406 条目化导出 | `/exports` 创建/查询/下载 | `ExportFormat{PDF,XLSX}`、`ExportType{CLAUSES,STANDARD_SUMMARY}`、下载审计 |
| #407 金融标准问答 | `POST /chat`、`POST /chat/stream` | 与 #114 共用；引用含 `standardNo/clauseNo` |

---

## 4. 统一规范核对

| 规范 | 要求 | 落地 | 结论 |
|---|---|---|---|
| BasePath | `/api/v1` | 4 个 server（Java 8081 / Python 8100 / Mock 4010 / Web 18080）+ `/api/v1/**` | 通过 |
| 响应信封 | `{code,message,data,traceId}` | `ApiEnvelope`，成功 `OK`，失败 `ErrorInfo` | 通过 |
| 错误码分段 | `AUTH/PERM/PARAM/BIZ/SYS/RATE` | `x-error-code-policy`：AUTH→401、PERM→403、PARAM→400、BIZ→404/409/422、SYS→500/501/503、RATE→429 | 通过 |
| 鉴权 | Bearer | 全局 `security: bearerAuth`，仅 `/healthz /readyz /system/info /auth/login` 例外 | 通过 |
| 分页 | `page/pageSize` + `{items,total,page,pageSize}` | `Page/PageSize` 参数 + `*Page` schema | 通过 |
| 幂等 | `Idempotency-Key` | 所有写接口声明 `x-idempotency: required`（状态变更类）；24h、同 key 不同体重放 409 | 通过 |
| SSE | 事件格式固定 | `SseMeta/Delta/Citation/Done/Error` + `ping` | 通过 |
| 版本与兼容 | 破坏性变更提前 ≥2 个版本告警 | `x-compatibility-policy` + `Deprecation/Sunset/Link` 响应头 | 通过 |
| 权限点 | 写接口必须声明 | 100% 声明 `x-permission` | 通过 |
| 审计点 | 写接口必须声明 | 100% 声明 `x-audit`；两个只读计算 POST 显式标注 `required:false` 及原因 | 通过 |

---

## 5. 写接口权限与审计矩阵（摘要）

> 完整 71 项见契约 `x-permission` / `x-audit` 字段。下表列出状态变更类写接口（`Idempotency-Key` 必填项以 ★ 标注）。

| 接口 | 权限点 | 审计动作 |
|---|---|---|
| POST /auth/login | none | auth.login / auth.login.failed |
| POST /auth/logout | authenticated | auth.logout |
| POST /roles | role:write | role.create |
| PUT /roles/{roleId}/permissions | role:write | role.permission.update |
| POST /knowledge-bases | kb:write | kb.create |
| PATCH /knowledge-bases/{kbId} | kb:write | kb.update |
| DELETE /knowledge-bases/{kbId} | kb:write | kb.delete |
| POST /knowledge-bases/{kbId}/documents ★ | doc:upload | doc.upload / permission.denied |
| DELETE /documents/{documentId} | doc:delete | doc.delete |
| POST /documents/{documentId}:reparse ★ | doc:write | doc.reparse |
| PUT /documents/{documentId}/metadata | meta:write | meta.update |
| POST /documents/{documentId}/chunks:preview | doc:read | 只读计算（required:false） |
| POST /ingest/tasks/{taskId}:retry ★ | ingest:retry | ingest.retry |
| POST /index/embedding-jobs ★ | index:write | index.embed |
| POST /index/embedding-jobs/{jobId}:pause | index:write | index.embed.pause |
| POST /index/embedding-jobs/{jobId}:resume | index:write | index.embed.resume |
| POST /index/builds ★ | index:build | index.build |
| POST /index/builds/{buildId}:retry ★ | index:build | index.build.retry |
| POST /retrieve | retrieval:query | retrieval.query / permission.denied |
| POST /retrieve/query-understanding | retrieval:query | 只读计算（required:false） |
| POST /chat ★ | chat:use | chat.query / security.input.blocked / security.prompt_injection.blocked |
| POST /chat/stream ★ | chat:use | chat.query |
| POST /documents/{documentId}/summaries ★ | summary:generate | summary.generate |
| POST /exports ★ | export:create | export.create |
| GET /exports/{exportId}/download | export:read | export.download |
| PUT /security/policies | security:write | security.policy.update |
| POST /security/checks | security:check | security.check / security.check.blocked |
| POST /admin/users | user:write | user.create |
| PATCH /admin/users/{userId} | user:write | user.update |
| PUT /admin/system-config | config:write | config.update |
| POST /models/chat | model:invoke | model.invoke |
| POST /models/embeddings | model:invoke | model.embed |

**越权拦截**：所有数据级操作在权限点之后追加对象归属校验（`kbId/documentId` ∈ 当前租户且用户有权），失败统一 `PERM.FORBIDDEN` + `permission.denied` 审计（#252）。

---

## 6. 边界评审：未新增 P1+ 能力

| 检查项 | 结论 |
|---|---|
| 是否出现混合检索/重排接口 | 否。`retrievalMode` 枚举仅 `vector`，`rerank` 默认 false |
| 是否出现多轮会话接口 | 否。仅预留 `sessionId/conversationMode` 字段 |
| 是否出现 SSO/OAuth/MFA | 否。仅 #166 基础账号登录 |
| 是否出现多租户管理接口 | 否。仅字段预留 `tenantId` |
| 是否出现定时调度接口 | 否。任务仅事件驱动（上传触发 / 手动重试） |
| 是否出现统一网关能力（分布式限流/灰度/路由） | 否。P0 反向代理 + Resilience4j 在服务内完成最小限流/熔断/降级 |
| 是否出现密级/域/生效期过滤逻辑 | 否。字段预留，不做过滤 |
| 是否出现 Agent/Skill 能力实现 | 否。`capability` 枚举仅 RAG 可用（SKILL 为 P1+ 预留） |

---

## 7. 契约与骨架一致性核对

| 检查 | 结果 |
|---|---|
| Java 骨架 `/healthz`、`/readyz`、`/api/v1/system/info` | 已验证 200；`/api/v1/**` 未实现接口返回 501 `SYS.NOT_IMPLEMENTED`；无 Token 返回 401 `AUTH.UNAUTHENTICATED` |
| Python 骨架 `/healthz`、`/readyz`、`/api/v1/system/info`、`/metrics` | 已验证 200；其余 `/api/v1/**` 返回 501 |
| Mock 覆盖核心链路 | 登录→建库→上传→任务→分块→问答→SSE→越权→审计，23/23 冒烟通过（容器内运行） |
| 前端客户端 | `web/src/api/p0Client.js` 覆盖 71 个 operation 的调用封装；nginx `/api/` 反代已验证 |

---

## 8. 评审遗留项（不阻塞冻结）

| # | 事项 | 处理 |
|---|---|---|
| 1 | `AUTH.TOKEN_EXPIRED` 与 `AUTH.UNAUTHENTICATED` 的客户端刷新策略 | P0 由前端跳转登录；刷新令牌 P1 |
| 2 | 分页上限（`pageSize` 最大 200）需在实现层固化 | 实现阶段在 `PageSize` 校验 |
| 3 | `x-audit` 仅声明接口级动作，字段级脱敏规则 | P0 实现时按 `AuditLog.detail` 白名单 |
| 4 | Prometheus 指标命名待与运维对齐 | P0 使用 Micrometer 默认命名 |
| 5 | 多语言错误消息（i18n） | P1 |

---

## 9. 评审签署

| 角色 | 结论 | 备注 |
|---|---|---|
| 架构 | 通过 | 契约冻结为 `1.0.0-p0`；变更走版本流程 |
| 安全 | 通过（条件） | 条件：实现阶段完成 `AuditLog.detail` 脱敏白名单（遗留项 3） |
| 前端 | 通过 | 可基于 mock 独立联调 |
| 后端 | 通过 | 71 个 operation 均可映射到 Java/Python 分工 |

**冻结声明**：自本记录起，`aipaas-openapi-v1.yaml` 仅允许「兼容性变更」；破坏性变更须按 [VERSIONING.md](../../aipaas-contracts/VERSIONING.md) 提前 ≥2 个次版本通过 `Deprecation`/`Sunset` 头告警。
