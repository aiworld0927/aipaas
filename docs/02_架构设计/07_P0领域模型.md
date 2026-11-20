# P0 领域模型（冻结版 v1.0.0-p0）

> 状态：**Frozen**（Step 0 交付物）
> 契约来源：[aipaas-openapi-v1.yaml](../../aipaas-contracts/openapi/aipaas-openapi-v1.yaml)（`components.schemas` 为唯一权威定义，本文为可读说明）
> 适用范围：P0 共 44 项功能、10 个功能域。**不实现业务逻辑**，只冻结结构、命名、枚举与扩展点。

---

## 1. 建模原则

| 原则 | 说明 |
|---|---|
| 统一信封 | 所有 HTTP 响应 `{code,message,data,traceId}`；`code="OK"` 表示成功，其余为分段错误码。领域模型只描述 `data` 内的结构。 |
| 多租户预留 | 所有业务实体自 P0 起携带 `tenantId`，默认值 `default`；P1 #131 开启多租户时无需改表结构。 |
| 时间统一 | 所有时间为 RFC 3339 UTC（`format: date-time`），如 `2026-11-17T08:00:00Z`。 |
| ID 约定 | 字符串 ID，服务端生成，格式 `前缀_随机串`（如 `kb_`、`doc_`、`task_`、`chk_`、`ans_`）。客户端不得解析 ID 内部结构。 |
| 预留字段 | 用 `x-reserved-for: P1|P2` 标注；P0 **可空、不参与逻辑**，但必须出现在契约中，避免后续破坏性变更。 |
| 只增不删 | 枚举值只增不删；客户端必须容忍未知枚举值（见 [VERSIONING.md](../../aipaas-contracts/VERSIONING.md)）。 |

---

## 2. 实体关系总览

```
Tenant(default)
  └── KnowledgeBase (kbId)
        ├── Document (documentId) ── StandardMeta (1:1 标准元数据)
        │     ├── IngestTask (1:N，每次上传/重解析生成)
        │     ├── Chunk (1:N，含 clauseNo/chapterPath/pageNo)
        │     ├── Clause (1:N，条款锚点，由解析产生)
        │     └── Summary (1:1 最新标准简介，P0 #405)
        ├── IndexBuild / EmbeddingJob (1:N，知识构建与索引)
        └── RetrieveHit* ── Citation* (问答引用，指向 Chunk)

ChatRequest ──> Answer ──> Citation[] (+ TokenUsage)
User ── Role ── Permission
AuditLog (横切：所有写操作 + 敏感读操作)
```

关系约束：

- `KnowledgeBase 1 — N Document`；`Document 1 — N IngestTask`（重解析新增任务，历史保留可追溯）。
- `Document 1 — N Chunk`；`Chunk` 与 `Clause` 通过 `clauseNo` 关联，条款级检索以 `Chunk.chunkType=CLAUSE` 表达。
- `RetrieveHit`/`Citation` 均指向已存在的 `chunkId`，保证引用可回溯（#408 引用溯源）。
- `AuditLog` 不挂在业务实体下，而是按 `objectType/objectId` 关联。

---

## 3. 核心模型定义

### 3.1 KnowledgeBase（知识库，#130 / #246）

| 字段 | 类型 | 必填 | P0 语义 | 预留 |
|---|---|---|---|---|
| id | string | ✓ | 知识库 ID | |
| tenantId | string(`default`) | ✓ | 租户 | P1 #131 |
| name | string(1-128) | ✓ | 名称 | |
| description | string(≤1024) | | 描述 | |
| status | enum `ACTIVE/ARCHIVED/DELETED` | ✓ | 生命周期 | |
| documentCount | int ≥0 | ✓ | 文档数（聚合） | |
| chunkCount | int ≥0 | ✓ | 分块数（聚合） | |
| indexStatus | enum `EMPTY/BUILDING/READY/FAILED/STALE` | ✓ | 索引状态 | |
| domain | string \| null | | 空 | **P2** |
| version | string \| null | | 空（配置版本） | **P2** |
| env | string \| null | | 空（环境标识） | **P2** |
| createdAt / updatedAt | date-time | ✓ | 审计时间 | |

### 3.2 Document（文档，#1 / #134）

| 字段 | 类型 | 必填 | P0 语义 |
|---|---|---|---|
| id | string | ✓ | 文档 ID |
| tenantId | string | ✓ | 租户（默认 `default`） |
| kbId | string | ✓ | 归属知识库 |
| fileName | string | ✓ | 原始文件名 |
| format | enum `PDF/DOC/DOCX/XLS/XLSX/PPT/PPTX/MD/TXT` | ✓ | 格式校验依据（#1） |
| sizeBytes | int ≥0 | ✓ | 大小校验；≥100MB 需分片/流式不中断 |
| status | enum `QUEUED/PARSING/PROCESSING/READY/FAILED` | ✓ | 文档生命周期 |
| standardNo | string \| null | | 金融标准编号（如 `JR/T 0197-2020`） |
| uploaderId | string | ✓ | 上传者（越权拦截与审计依据，#252） |
| source | string | ✓ | 来源，P0 固定 `manual_upload` |
| pageCount / clauseCount | int \| null | | 解析结果统计 |
| lastTaskId | string \| null | | 最近一次导入任务 |
| errorCode / errorMessage | string \| null | | 失败可解释（#18/#21） |
| createdAt / updatedAt | date-time | ✓ | |

### 3.3 IngestTask（导入任务，#10 / #18 / #19 / #20 / #21）

| 字段 | 类型 | 必填 | P0 语义 |
|---|---|---|---|
| id | string | ✓ | 任务 ID |
| tenantId | string | ✓ | 租户 |
| documentId / kbId | string | ✓ | 关联对象 |
| status | enum `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELED` | ✓ | 任务状态（实时可见） |
| stage | enum `UPLOAD/PARSE/CLEAN/CHUNK/EMBED/INDEX/DONE` | ✓ | 当前阶段 |
| progress | int 0-100 | ✓ | 进度 |
| retryCount | int ≥0 | ✓ | 已重试次数 |
| maxRetries | int，默认 2 | ✓ | 上限 2 次（#18/#21 验收） |
| error | `IngestTaskError{code,message,retryable}` | | 失败原因 |
| queuedAt / startedAt / finishedAt | date-time | | 阶段时间 |

**状态机**（P0 事件驱动，无定时调度）：

```
QUEUED ──> RUNNING ──> SUCCEEDED
              │  └──> FAILED（retryCount < maxRetries 可 :retry）
              └──────> CANCELED（用户取消，P1 开放入口）
```

重试约束：`:retry` 幂等键必填；服务端保证**不产生重复 Chunk**（按 documentId 版本覆盖）。

### 3.4 Chunk（分块，#36 / #37 / #44 / #57）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| id | string | ✓ | 分块 ID |
| tenantId | string | ✓ | 租户 |
| documentId / kbId | string | ✓ | 归属 |
| chunkType | enum `FIXED/STRUCTURED/CLAUSE` | ✓ | 固定长度/结构化/条款级 |
| chunkIndex | int ≥0 | ✓ | 文档内序号（稳定排序） |
| text | string | ✓ | 分块正文 |
| tokenCount | int | ✓ | 供向量化预算 |
| clauseNo | string \| null | | 条款号（条款锚点） |
| chapterPath | string \| null | | 章节路径，如 `第3章 > 3.2` |
| pageNo | int \| null | | 原页码 |
| metadata | object | | 文档级元数据快照（#57） |
| securityLevel / domain / effectiveAt | string \| null | | **P2 预留**（空） |
| indexVersion / env | string \| null | | **P2 预留**（空） |

> P0 相似度检索过滤仅基于 `kbId + 权限`；`securityLevel/domain` 不参与过滤逻辑。

### 3.5 Clause（条款，#404）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| clauseNo | string | ✓ | 条款编号（可重复于不同文档，需配合 documentId 定位） |
| title | string | ✓ | 条款标题 |
| chapterPath | string | ✓ | 章节路径 |
| text | string | ✓ | 条款正文 |
| pageNo | int \| null | | 页码 |
| level | int | ✓ | 层级（1=章，2=节，3=条） |
| chunkId | string \| null | | 对应分块（有则便于引用溯源） |

### 3.6 StandardMeta（标准元数据，#57 / #405）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| documentId / tenantId | string | ✓ | 关联文档 |
| standardNo | string | ✓ | 标准号 |
| standardName | string | ✓ | 标准名称 |
| issuer | string | ✓ | 发布机构 |
| issuedAt / replacedBy | string \| null | | 发布日期 / 被替代标准 |
| clauseCount | int ≥0 | ✓ | 条款数 |
| confidence | number 0-1 | ✓ | 抽取置信度 |
| source | enum `AUTO_EXTRACTED/MANUAL_CORRECTED` | ✓ | 抽取来源（默认 AUTO_EXTRACTED） |
| effectiveAt / domain / securityLevel | string \| null | | **P2 预留**（空） |
| version / env | string \| null | | **P2 预留**（空） |
| updatedAt | date-time | ✓ | |

### 3.7 RetrieveRequest / RetrieveHit（检索引擎，#63 / #70 / #88）

`RetrieveRequest`：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| query | string | — | 必填，归一化见 #63 |
| kbId | string \| null | | 单库检索 |
| kbIds | string[] | `[]` | **P2 预留**，与 `kbId` **互斥**；P0 传空 |
| topK | int | 5 | 返回条数 |
| retrievalMode | enum `[vector]` | `vector` | P0 仅向量召回；混合检索 P1 |
| rerank | boolean | `false` | P0 固定 false；重排 P1 |
| filters | `RetrievalFilters` | | 元数据过滤（#57） |

`RetrieveHit`：`rank, score, chunkId, documentId, kbId, text` 必填；`clauseNo, chapterPath, pageNo, standardNo, standardName, citation` 可选（引用溯源）。

**权限过滤（#88 / #252）**：命中集合在服务端完成知识库/文档级权限裁剪后再返回，**0 泄露**；越权访问记录 `permission.denied` 审计。

### 3.8 ChatRequest / Answer / Citation（生成与推理，#98–#114 / #405 / #407）

`ChatRequest`：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| query | string | — | 必填；输入校验（#108）+ 注入检测（#159） |
| kbId | string \| null | | 指定知识库 |
| kbIds | string[] | `[]` | **P2 预留**，与 `kbId` 互斥 |
| sessionId | string \| null | | **P1 预留**：多轮会话 |
| conversationMode | enum `[SINGLE_TURN]` | `SINGLE_TURN` | **P1 预留**：多轮模式 |
| retrievalMode | enum `[vector]` | `vector` | 同检索 |
| rerank | boolean | `false` | P1 预留 |
| topK | int | 5 | 召回条数 |
| capability | enum `RAG` / `SKILL` | `RAG` | P0 仅 RAG（#402 能力选择器只展示 RAG） |
| filters | `RetrievalFilters` | | 元数据过滤 |

`Answer`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| answerId | string | ✓ | 答案 ID |
| sessionId | string \| null | | **P1 预留** |
| content | string | ✓ | Markdown 正文；拒答时为空或引导语 |
| format | enum `[MARKDOWN]` | ✓ | 格式化输出（#110） |
| citations | `Citation[]` | ✓ | 引用溯源，无依据时为空 |
| refused | boolean | ✓ | 拒答标记（#106：无依据不得编造） |
| refusalReason | string \| null | | 拒答原因 |
| confidence | number 0-1 | ✓ | 置信度 |
| usage | `TokenUsage` | | token 统计 |
| finishReason | enum `STOP/LENGTH/CONTENT_FILTER/REFUSED/ERROR` | ✓ | |
| retrievalMode / rerank | | ✓ | 回显本次检索策略 |
| promptTemplateVersion | string | | Prompt 版本（#103） |
| traceId | string | ✓ | 全链路 Trace（#175） |
| createdAt | date-time | ✓ | |

`Citation`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| chunkId / documentId | string | ✓ | 引用定位 |
| standardNo / standardName / clauseNo | string \| null | | 标准信息 |
| pageNo | int \| null | | **P1 预留** |
| chapterPath | string \| null | | **P1 预留** |
| quote | string \| null | | **P1 预留**：原文摘录 |
| score | number | ✓ | 相关度 |

### 3.9 Role / Permission（#139 / #193）

`Role`：`id, tenantId, code, name, builtIn, permissions[string], userCount`，时间戳。
`Permission`：`code, name, domain, description`。

权限点命名：`<资源>:<动作>`，如 `kb:read/doc:upload/chat:use/index:build/audit:read`。
P0 内置角色：`PLATFORM_ADMIN`（全量）、`KB_ADMIN`（知识库管理）、`KB_USER`（只读+问答）、`AUDITOR`（仅审计只读）。

**权限判定顺序**：认证 → 权限点 → 数据级（kbId/documentId 归属）→ 业务规则；任一步失败写 `permission.denied` 审计并返回 `PERM.FORBIDDEN`（403）。

### 3.10 AuditLog（#144 / #252）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| id | string | ✓ | |
| tenantId | string | ✓ | |
| actorId / actorName | string | ✓ | 操作者 |
| action | string | ✓ | 动作码，如 `doc.upload` |
| objectType / objectId | string | ✓ / 可空 | 对象 |
| result | enum `SUCCESS/FAILURE/DENIED` | ✓ | |
| ip / userAgent | string \| null | | 来源 |
| traceId | string | ✓ | 与请求 Trace 对齐 |
| detail | object | | 变更摘要（不含敏感明文） |
| createdAt | date-time | ✓ | 保留 ≥180 天（#144） |

---

## 4. 枚举目录（P0 冻结）

| 枚举 | 取值 |
|---|---|
| DocumentFormat | `PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, MD, TXT` |
| DocumentStatus | `QUEUED, PARSING, PROCESSING, READY, FAILED` |
| IngestTaskStatus | `QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELED` |
| IngestStage | `UPLOAD, PARSE, CLEAN, CHUNK, EMBED, INDEX, DONE` |
| ChunkType | `FIXED, STRUCTURED, CLAUSE` |
| RetrievalMode | `vector`（P1 增 hybrid） |
| CapabilityType | `RAG, SKILL`（P0 仅 RAG 可用） |
| ConversationMode | `SINGLE_TURN`（P1 增 MULTI_TURN） |
| AnswerFormat | `MARKDOWN` |
| FinishReason | `STOP, LENGTH, CONTENT_FILTER, REFUSED, ERROR` |
| AuditResult | `SUCCESS, FAILURE, DENIED` |
| KnowledgeBaseStatus | `ACTIVE, ARCHIVED, DELETED` |
| IndexState | `EMPTY, BUILDING, READY, FAILED, STALE` |
| JobStatus | `QUEUED, RUNNING, PAUSED, SUCCEEDED, FAILED` |
| ExportFormat | `PDF, XLSX` |
| ExportType | `CLAUSES, STANDARD_SUMMARY` |
| SecurityCheckType | `INPUT_CONTENT, OUTPUT_CONTENT, PROMPT_INJECTION` |
| UserStatus | `ACTIVE, DISABLED, LOCKED` |
| ModelStatus | `UP, DEGRADED, DOWN` |

---

## 5. SSE 事件模型（流式问答 #113 / 任务进度 #10）

端点：`POST /api/v1/chat/stream`、`GET /api/v1/ingest/tasks/{taskId}/events`

| 事件 | data 结构 | 说明 |
|---|---|---|
| `meta` | `SseMeta{answerId,sessionId?,traceId,retrievalMode,startedAt}` | 流开始 |
| `delta` | `SseDelta{answerId,seq,delta}` | 增量文本；`seq` 同时作为 SSE `id`，支持 `Last-Event-ID` 续传 |
| `citation` | `SseCitation{answerId,citations[]}` | 引用事件，可多次 |
| `done` | `SseDone{answerId,finishReason,usage?,refused?,confidence?}` | 正常结束 |
| `error` | `SseError{code,message,retryable}` | 流内错误（HTTP 仍为 200） |
| `ping` | 空/心跳对象 | 保活，客户端忽略 |

格式：每行一个 JSON，`event: <type>` + `data: <json>`；**不允许多行 data**。

---

## 6. P1 / P2 预留字段矩阵（P0 全部可空、不参与逻辑）

| 模型 | 字段 | 预留版本 | 用途 |
|---|---|---|---|
| 全部业务实体 | `tenantId` | P1 #131 | 多租户隔离（P0 默认 `default`） |
| ChatRequest / Answer | `sessionId`, `conversationMode` | P1 | 多轮对话 |
| Citation | `pageNo`, `chapterPath`, `quote` | P1 | 精细化引用 |
| Chunk | `securityLevel`, `domain`, `effectiveAt`, `indexVersion`, `env` | P2 | 密级/域/生效期/索引版本 |
| StandardMeta | `securityLevel`, `domain`, `effectiveAt`, `version`, `env` | P2 | 标准治理 |
| KnowledgeBase | `domain`, `version`, `env` | P2 | 配置版本与环境 |
| RetrieveRequest / ChatRequest | `kbIds[]`（与 `kbId` 互斥，默认 `[]`） | P2 | 多库联合检索 |
| IndexBuildRequest / IndexBuild | `indexType`, `metric`, `params`, `indexSlot`, `alias`, `version`, `env` | P2 | HNSW 参数化、在线/离线索引槽与别名切换 |

> 校验规则：`kbId` 与 `kbIds` 同时非空 → `PARAM.INVALID`（400）。

---

## 7. 明确不在 P0 模型内的能力

| 能力 | 归属 | 说明 |
|---|---|---|
| 混合检索 / 重排 | P1 | `retrievalMode` 仅 `vector`，`rerank` 固定 false |
| 多轮会话 / 长期记忆 | P1 | `sessionId`/`conversationMode` 仅预留 |
| 多租户隔离策略 | P1 #131 | 仅 `tenantId` 字段 |
| 定时调度（XXL-JOB） | P1 #195 | P0 仅事件驱动任务队列 |
| 统一 API 网关（限流/熔断/灰度） | P1 #204–#210 | P0 反向代理 + Resilience4j 最小治理 |
| 密级/域/生效期过滤 | P2 | 字段预留，不参与逻辑 |
