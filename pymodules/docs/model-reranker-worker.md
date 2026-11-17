# 重排序模型服务（apps/model-reranker-worker）

> 返回总文档：[README.md](../README.md)

## 1. 职责与定位

`model-reranker-worker` 是独立的 Cross-Encoder 重排序推理服务，对 `query` 与候选文档逐一打分，分数越高表示相关性越强。查询服务在 RRF 融合后调用它做精排，再按断崖规则截断。

支持两种后端：

- **offline**：本地 `bge-reranker-large`（FlagEmbedding `FlagReranker`）。
- **online**：DashScope `TextReRank` 接口（默认模型 `qwen3-rerank`）。

- Python 包名：`model_reranker_worker`
- distribution 名：`model-reranker-worker`
- 默认端口：`8004`
- 独立部署：是（自带 Dockerfile，复用 torch 基座镜像）

## 2. 目录结构

```
apps/model-reranker-worker/
├── Dockerfile
├── pyproject.toml
└── model_reranker_worker/
    ├── api/
    │   ├── app.py                 # 应用入口与 lifespan
    │   └── routers/
    │       ├── health.py          # /health、/ready、/v1/model/info
    │       └── inference.py       # /v1/rerank
    ├── service.py                 # 后端门面：按 backend 分派
    ├── model_service.py           # 离线 FlagReranker 实现
    └── online_service.py          # 在线 DashScope 实现
```

## 3. 启动与配置

### 3.1 启动

```bash
uv sync --all-packages --group cpu   # GPU 环境用 --group gpu

cd /home/aipaas/aipaas/pymodules
uv run python -m model_reranker_worker.api.app
```

入口先 `load_settings("model_reranker_worker")`。lifespan 启动阶段调用 `service.ensure_ready()`：

- 离线：在线程池中加载 `FlagReranker`，并发调用只加载一次。
- 在线：校验 `online.api_key` 是否存在。

模型加载失败不中断进程，`/health` 仍返回 200，`/ready` 返回 503。

### 3.2 配置命名空间 `model_reranker_worker`

配置 schema 见 [model_reranker_worker.py](../packages/common/aipaas_common/conf/schemas/model_reranker_worker.py)。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `backend` | `offline` | `offline` 或 `online` |
| `host` / `port` | `0.0.0.0` / `8004` | 可被 `MODEL_RERANKER_WORKER_HOST/PORT` 覆盖 |
| `max_documents` | `128` | 单次请求最大文档数，超出返回 413 |
| `max_concurrency` | `1` | 推理并发闸门，默认串行 |
| `offline.model_path` | `/app/models/bge-reranker-large` | 本地模型目录 |
| `offline.device` | `null` | 为空时自动探测 `cuda` / `cpu` |
| `offline.use_fp16` | `true` | 半精度推理 |
| `offline.normalize` | `true` | 固定开启；业务断崖阈值按归一化分数设定 |
| `online.api_key` | 环境变量 | `TEXT_RERANK_API_KEY`，回退 `OPENAI_API_KEY` |
| `online.model` | `qwen3-rerank` | 在线重排模型 |
| `online.instruct` | `针对给定的查询，检索能够解答该查询的相关段落` | 在线接口指令 |

### 3.3 环境变量

| 变量 | 用途 |
|---|---|
| `TEXT_RERANK_API_KEY` | 在线后端密钥（回退 `OPENAI_API_KEY`） |
| `MODEL_RERANKER_WORKER_HOST/PORT` | 覆盖监听地址/端口 |
| `RERANKER_SERVICE_URL` | 业务侧消费方配置，默认 `http://svc-model-reranker:8004` |
| `APP_ENV` / `CONFIG_DIR` | 配置环境与目录 |

## 4. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针，不检查模型状态 |
| GET | `/ready` | 就绪探针；未就绪返回 503 |
| GET | `/v1/model/info` | 当前后端、模型、设备及 `loaded` 状态 |
| POST | `/v1/rerank` | 文档重排序打分 |

### 4.1 POST /v1/rerank

请求：

```json
{
  "query": "怎么测交流电压？",
  "documents": ["文档片段1", "文档片段2"]
}
```

响应：

```json
{"scores": [0.923, 0.107]}
```

- `scores` 与 `documents` **顺序一一对应**，不是按分数排序后的结果。
- 离线后端使用归一化分数（`normalize=true`）。

错误码：

| 状态码 | 场景 |
|---|---|
| 400 | `query` 或 `documents` 为空 |
| 413 | 文档数超过 `max_documents` |
| 503 | 服务尚未就绪 |
| 500 | 打分异常（详情带回错误原因） |

## 5. 实现要点

### 5.1 后端分派

[service.py](../apps/model-reranker-worker/model_reranker_worker/service.py) 按 `module.is_online` 延迟导入 `online_service` 或 `model_service`，在线模式无需加载 `FlagEmbedding` / torch。

### 5.2 离线打分

[model_service.py](../apps/model-reranker-worker/model_reranker_worker/model_service.py)：

- `FlagReranker(model_name_or_path, device, use_fp16)`，模块级单例 + `asyncio.Lock` 防并发重复加载。
- `compute_score([[query, doc], ...], normalize=...)`；单条输入可能返回标量，统一转换为 `list[float]`，并兼容 `.tolist()`。
- 推理放在 `asyncio.to_thread`，由 `Semaphore(max_concurrency)` 限流。

### 5.3 在线打分

[online_service.py](../apps/model-reranker-worker/model_reranker_worker/online_service.py)：

- `dashscope.TextReRank.call(api_key, model, query, documents, top_n=len(documents), return_documents=False, instruct=...)`。
- 由于 SDK 为同步实现，统一用 `asyncio.to_thread` 包装。
- 按响应中的 `index` 把 `relevance_score` 回填到与输入等长的分数数组，保证顺序一致。
- 非 200 状态码抛出 `RuntimeError`，由路由转为 500。

## 6. 构建与部署

与嵌入服务相同，依赖 torch 基座镜像，先构建基座再构建应用。

### 6.1 构建基座（如未构建）

```bash
cd /home/aipaas/aipaas/pymodules
docker build --network host --build-arg TORCH_VARIANT=gpu \
  -f docker/base/Dockerfile -t aipaas/aipaas-torch-gpu-base:local .
```

基座说明见 [docker/base/Dockerfile](../docker/base/Dockerfile) 与总文档「部署与镜像」。

### 6.2 构建应用镜像

[Dockerfile](../apps/model-reranker-worker/Dockerfile)：

1. 以 `BASE_IMAGE` 为基础（默认 `aipaas/aipaas-torch-gpu-base:local`）。
2. 校验 `uv.lock` sha256 与基座 `/app/.uv-lock-sha256` 一致。
3. 复制源码与配置，仅对 `packages/common` 和 `apps/model-reranker-worker` 做 `editable install`（`--no-deps --no-build-isolation`）。
4. 暴露 `8004`，健康检查 `/health`（`start-period=120s`），启动命令 `python -m model_reranker_worker.api.app`。

```bash
docker build --network host \
  --build-arg BASE_IMAGE=aipaas/aipaas-torch-gpu-base:local \
  -f apps/model-reranker-worker/Dockerfile \
  -t aipaas/svc-model-reranker:latest .
```

只改业务代码时约 30 秒完成且无需联网；修改 `uv.lock` 后必须先重建基座。

### 6.3 运行提示

- 模型目录挂载到 `/app/models`（默认 `/app/models/bge-reranker-large`）。
- 数据卷：`/app/logs`、`/app/models`。
- GPU 部署需容器可见 GPU；`offline.device` 留空可自动探测。

## 7. 依赖关系

```
model-reranker-worker
└── aipaas-common（统一日志/配置）
```

第三方依赖：FastAPI、Uvicorn、python-dotenv、numpy、transformers、dashscope。

`torch`、`FlagEmbedding` 不在此声明，由根 workspace 的 `cpu|gpu` 分组提供，避免与分组索引冲突。

## 8. 注意事项

- **分数顺序**：接口返回的是逐文档分数，不是排序结果；调用方需自行按分数降序排序。
- **归一化**：`offline.normalize=true` 是业务断崖阈值（查询服务 `rerank_gap_abs=0.5`、`rerank_gap_ratio=0.25`）的前提，不建议关闭。
- **`/health` 不代表模型可用**：编排就绪探针应使用 `/ready`。
- **批量上限**：默认 128 篇，超出需调用方分批。
- **基座一致性**：`uv.lock` 变更后未重建基座会导致应用镜像构建直接失败。
