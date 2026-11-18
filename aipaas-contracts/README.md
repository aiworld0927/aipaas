# aipaas-contracts

P0 接口契约的唯一权威来源。前端、Java 平台、Python AI 服务与 mock 均以此为准，**不得各自维护副本**。

## 目录

```
aipaas-contracts/
├── openapi/
│   └── aipaas-openapi-v1.yaml   # OpenAPI 3.1 契约（冻结版 1.1.0-p0）
├── mock/
│   ├── server.py                # 零依赖契约 mock（标准库实现）
│   ├── smoke_upload_qa.py       # 上传 → 问答 串联冒烟脚本
│   └── Dockerfile
├── scripts/                     # 契约校验/打包脚本
└── VERSIONING.md                # 版本与兼容策略（对齐 NFR#31）
```

## 规模与覆盖

| 指标 | 数值 |
|---|---|
| OpenAPI 版本 | 3.1.0 |
| 契约版本 | `1.1.0-p0`（`info.x-contract-status: frozen`） |
| path / operation | 70 / 80 |
| schema | 177 |
| 覆盖 P0 功能 | 44 / 44（10 个功能域） |

## 统一规范速查

| 项 | 约定 |
|---|---|
| BasePath | `/api/v1` |
| 响应信封 | `{code, message, data, traceId}`；成功 `code="OK"` |
| 错误码 | 分段 `AUTH / PERM / PARAM / BIZ / SYS / RATE`，HTTP 状态映射见 `info.x-error-code-policy` |
| 鉴权 | `Authorization: Bearer <token>`；`/auth/login`、健康检查免鉴权 |
| 分页 | 请求 `page`(≥1, 默认 1)、`pageSize`(1-200, 默认 20)；响应 `{items,total,page,pageSize}` |
| 幂等 | 写接口 `Idempotency-Key`（UUID）；24h 内同 key 同 body 返回首次结果，不同 body → 409 `BIZ.CONFLICT` |
| Trace | 请求/响应头 `X-Trace-Id`，与信封 `traceId` 一致 |
| SSE | `event: meta|delta|citation|done|error|ping`，单行 JSON `data`，`delta.seq` 作为 `id` 支持 `Last-Event-ID` |
| 版本 | URL 主版本 `/v1`；破坏性变更提前 ≥2 个次版本告警，见 [VERSIONING.md](./VERSIONING.md) |

## 扩展字段约定

| 字段 | 含义 |
|---|---|
| `x-p0-feature` | 该 operation 对应的 P0 功能编号（对齐功能清单） |
| `x-owner` | 实现归属（java / python） |
| `x-permission` | 所需权限点（如 `doc:upload`）；`none` 表示免鉴权 |
| `x-audit` | 审计声明 `{required, actions[]}`；只读计算接口为 `{required:false, reason}` |
| `x-idempotency` | 是否为状态变更写接口（`required`） |
| `x-reserved-for` | 字段预留阶段（`P1` / `P2`），P0 可空且不参与逻辑 |
| `x-error-code-policy` | 错误码分段与 HTTP 状态映射 |
| `x-compatibility-policy` | 兼容策略（提前告警版本数、Deprecation 头） |

## 使用方式

### 1. 启动 mock（前端/后端独立联调）

```bash
python3 aipaas-contracts/mock/server.py --port 4010
# 或
docker build -t aipaas/contracts-mock:latest aipaas-contracts/mock
docker run -d -p 4010:4010 aipaas/contracts-mock:latest
```

mock 账号：任意 `username`（≥3 字符）/ `password`（≥8 字符）；`admin` 拥有全量权限，`viewer`/`auditor` 为只读角色。

### 2. 一键串联验证「上传 → 问答」

```bash
python3 aipaas-contracts/mock/smoke_upload_qa.py --base-url http://127.0.0.1:4010
```

覆盖：登录 → 建知识库 → 上传（幂等键）→ 轮询导入任务 → 分块列表 → 问答 → SSE 流式 → 越权 403 → 审计校验。

### 3. 前端接入

- 客户端封装：`web/src/api/p0Client.js`
- 开发代理：`vite.config.js` 中 `/api` → `http://127.0.0.1:8081`
- 直连 mock：`VITE_PLATFORM_API_BASE=http://127.0.0.1:4010/api/v1 npm run dev`（注意跨域，mock 已开启 CORS）

### 4. 后端接入

- Java：`javaplat/apps/platform-api`（统一信封、Trace、幂等过滤器已在 `libs/common`）
- Python：`pymodules/apps/rag-ai-api`（FastAPI 骨架，未实现路由返回 501 `SYS.NOT_IMPLEMENTED`）

### 5. 契约校验

```bash
python3 - <<'PY'
import yaml
doc = yaml.safe_load(open('aipaas-contracts/openapi/aipaas-openapi-v1.yaml'))
assert doc['openapi'] == '3.1.0'
print('paths:', len(doc['paths']), 'schemas:', len(doc['components']['schemas']))
PY
```

## 变更流程

1. 在 `openapi/aipaas-openapi-v1.yaml` 修改，并同步 `x-p0-feature`/`x-permission`/`x-audit` 声明。
2. 运行 mock 与冒烟脚本，确认串联链路不回归。
3. 破坏性变更必须先走 [VERSIONING.md](./VERSIONING.md) 的告警流程（≥2 个次版本 + `Deprecation`/`Sunset` 头）。
4. 更新 `docs/architecture/01-p0-api-review.md` 的评审记录。
