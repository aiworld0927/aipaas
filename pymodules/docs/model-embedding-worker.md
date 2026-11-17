# 嵌入模型服务（apps/model-embedding-worker）

> 返回总文档：[README.md](../README.md)

## 1. 职责与定位

`model-embedding-worker` 是独立的向量推理服务，对上提供批量文本向量化能力。它支持两种后端：

- **offline**：本地 BGE-M3，同时输出稠密向量与稀疏向量（lexical weights），供 Milvus 混合检索使用。
- **online**：OpenAI 兼容的远端 embedding 接口（默认 DashScope `text-embedding-v4`），只输出稠密向量，稀疏向量固定为空列表。

业务侧（导入服务、查询服务）通过 `aipaas_model_clients.lm.embedding_http_utils` 以 HTTP 调用它，业务进程中不加载 torch/BGE-M3。

- Python 包名：`model_embedding_worker`
- distribution 名：`model-embedding-worker`
- 默认端口：`8003`
- 独立部署：是（自带 Dockerfile，基于 torch 基座镜像）

## 2. 目录结构

```
apps/model-embedding-worker/
├── Dockerfile
├── pyproject.toml
└── model_embedding_worker/
    ├── api/
    │   ├── app.py                 # 应用入口与 lifespan
    │   └── routers/
    │       ├── health.py          # /health、/ready、/v1/model/info
    │       └── inference.py       # /v1/embeddings
    ├── service.py                 # 后端门面：按 backend 分派
    ├── model_service.py           # 离线 BGE-M3 实现
    └── online_service.py          # 在线接口实现
```

## 3. 启动与配置

### 3.1 启动

```bash
uv sync --all-packages --group cpu   # GPU 环境用 --group gpu

cd /home/aipaas/aipaas/pymodules
uv run python -m model_embedding_worker.api.app
```

入口先 `load_settings("model_embedding_worker")`。lifespan 启动阶段调用 `service.ensure_ready()`：

- 离线：在线程池中加载 BGE-M3（并发调用只加载一次）。
- 在线：只校验 `online.api_key` 是否存在。

**模型加载失败不会中断进程**，`/health` 仍为 200，`/ready` 返回 503 并暴露原因，便于容器探针与运维定位。

### 3.2 配置命名空间 `model_embedding_worker`

配置 schema 见 [model_embedding_worker.py](../packages/common/aipaas_common/conf/schemas/model_embedding_worker.py)。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `backend` | `offline` | `offline` 或 `online` |
| `host` / `port` | `0.0.0.0` / `8003` | 可被 `MODEL_EMBEDDING_WORKER_HOST/PORT` 覆盖 |
| `max_batch_size` | `64` | 单次请求最大文本条数，超出返回 413 |
| `max_concurrency` | `1` | 推理并发闸门；GPU 显存有限默认串行 |
| `offline.model_path` | `/app/models/bge-m3` | 本地模型目录 |
| `offline.device` | `null` | 为空时自动探测：有 GPU 用 `cuda`，否则 `cpu` |
| `offline.use_fp16` | `true` | 半精度推理 |
| `offline.normalize_embeddings` | `true` | 固定开启，Milvus IP 检索归一化后等价余弦 |
| `online.api_key` | 环境变量 | `EMBEDDING_ONLINE_API_KEY`，回退 `OPENAI_API_KEY` |
| `online.base_url` | DashScope 兼容模式 | 远端接口地址 |
| `online.model` | `text-embedding-v4` | 在线模型名 |
| `online.dimension` | `1536` | 向量维度 |
| `online.timeout` | `60` | 请求超时（秒） |

### 3.3 环境变量

| 变量 | 用途 |
|---|---|
| `EMBEDDING_ONLINE_API_KEY` | 在线后端密钥（回退 `OPENAI_API_KEY`） |
| `MODEL_EMBEDDING_WORKER_HOST/PORT` | 覆盖监听地址/端口 |
| `EMBEDDING_SERVICE_URL` | 业务侧消费方配置，默认 `http://svc-model-embedding:8003` |
| `APP_ENV` / `CONFIG_DIR` | 配置环境与目录 |

## 4. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针，仅表示进程存活，不检查模型状态 |
| GET | `/ready` | 就绪探针；未就绪返回 503，就绪时返回模型信息 |
| GET | `/v1/model/info` | 当前后端、模型、设备、维度及 `loaded` 状态 |
| POST | `/v1/embeddings` | 批量生成向量 |

### 4.1 POST /v1/embeddings

请求：

```json
{"embedding_documents": ["如何测量交流电压？", "设备告警如何处理？"]}
```

响应：

```json
{
  "dense": [[0.01, -0.02, "..."], ["..."]],
  "sparse": [{"100": 0.747, "205": 0.664}, {"...": 0.0}]
}
```

- `dense` 与输入顺序一一对应；在线后端按响应 `index` 排序还原顺序。
- `sparse` 为 `{token_id: weight}` 字典列表；在线后端固定为 `[]`。

错误码：

| 状态码 | 场景 |
|---|---|
| 400 | `embedding_documents` 为空 |
| 413 | 条数超过 `max_batch_size` |
| 503 | 服务尚未就绪 |
| 500 | 向量生成异常（详情带回错误原因） |

## 5. 实现要点

### 5.1 后端分派

[service.py](../apps/model-embedding-worker/model_embedding_worker/service.py) 通过 `_backend()` 延迟导入：

- `module.is_online == true` → `online_service`
- 否则 → `model_service`

延迟导入保证在线模式不必加载 `pymilvus` / torch，启动更快、镜像可裁剪。

### 5.2 离线编码

[model_service.py](../apps/model-embedding-worker/model_embedding_worker/model_service.py)：

- `BGEM3EmbeddingFunction(model_name=model_path, device=..., use_fp16=..., normalize_embeddings=...)`。
- 模型为模块级单例，`asyncio.Lock` 保证并发首次加载只执行一次。
- `encode_documents` 的稀疏输出为 CSR 矩阵，按 `indptr` 逐行切分为 `{token_id: weight}`。
- 推理放在 `asyncio.to_thread`，并由 `Semaphore(max_concurrency)` 限流，避免阻塞事件循环和 GPU 过载。

### 5.3 在线编码

[online_service.py](../apps/model-embedding-worker/model_embedding_worker/online_service.py)：

- 使用 `AsyncOpenAI(api_key, base_url, timeout)`，客户端惰性创建并复用连接池。
- 调用 `embeddings.create(model, input=texts)`，按 `index` 排序后返回稠密向量。
- 稀疏向量的在线实现是后续扩展点，接口结构无需变化。

## 6. 构建与部署

本服务**不直接使用 `python:3.12-slim` 安装依赖**，而是复用 torch 基座镜像，避免每个 worker 重复下载约 12GB 的 torch。

### 6.1 先构建基座镜像

[docker/base/Dockerfile](../docker/base/Dockerfile)：

```bash
cd /home/aipaas/aipaas/pymodules
docker build --network host --build-arg TORCH_VARIANT=gpu \
  -f docker/base/Dockerfile -t aipaas/aipaas-torch-gpu-base:local .
# CPU：
docker build --network host --build-arg TORCH_VARIANT=cpu \
  -f docker/base/Dockerfile -t aipaas/aipaas-torch-cpu-base:local .
```

- 基座只装 `pyproject.toml` 的 `cpu|gpu` 分组与所有 workspace 成员的第三方依赖，不装本地包。
- 构建时校验 torch 变体：CPU 必须 `torch.version.cuda is None`，GPU 必须为 CUDA 12.8。
- 必须加 `--network host`，否则 `download.pytorch.org` / `pypi.nvidia.cn` 在 bridge 网络下可能超时。
- 基座把 `uv.lock` 的 sha256 写入 `/app/.uv-lock-sha256`。

### 6.2 再构建应用镜像

[Dockerfile](../apps/model-embedding-worker/Dockerfile)：

1. 以 `BASE_IMAGE`（默认 `aipaas/aipaas-torch-gpu-base:local`）为基础。
2. 校验当前 `uv.lock` 的 sha256 与基座记录一致，不一致直接失败，防止运行环境静默漂移。
3. 复制源码与配置后，只对 `packages/common` 和 `apps/model-embedding-worker` 做 `-e` 安装（`--no-deps --no-build-isolation`），不联网。
4. 暴露 `8003`，健康检查 `/health`（`start-period=120s`，为模型加载留时间），启动命令 `python -m model_embedding_worker.api.app`。

```bash
docker build --network host \
  --build-arg BASE_IMAGE=aipaas/aipaas-torch-gpu-base:local \
  -f apps/model-embedding-worker/Dockerfile \
  -t aipaas/svc-model-embedding:latest .
```

只改业务代码时构建约 30 秒且无需联网；**修改 `uv.lock` 后必须先重建基座镜像**。

### 6.3 运行提示

- 模型目录需挂载到 `/app/models`（默认模型路径 `/app/models/bge-m3`）。
- 日志与会话数据卷：`/app/logs`、`/app/models`。
- GPU 部署需容器可见 GPU，并设置 `backend=offline`、`offline.device=cuda`（或留空自动探测）。

## 7. 依赖关系

```
model-embedding-worker
└── aipaas-common（统一日志/配置）
```

第三方依赖：FastAPI、Uvicorn、python-dotenv、numpy、transformers、openai。

**注意**：`torch`、`FlagEmbedding`、`pymilvus[model]` 不在本服务 `pyproject.toml` 中声明，而是由根 workspace 的 `cpu|gpu` 分组提供。若直接写进成员依赖，会让 torch 走默认 PyPI 源解析并与分组索引冲突。

## 8. 注意事项

- `max_batch_size` 与 `max_concurrency` 共同决定吞吐；GPU 显存不足时优先下调 `max_batch_size` 或保持串行。
- 在线后端只返回 dense，消费方若依赖 sparse 混合检索需使用 offline。
- `/health` 不代表模型可用，编排探针应使用 `/ready`。
- 基座镜像与 `uv.lock` 强绑定：lock 变更后未重建基座会导致应用镜像构建失败，这是有意设计。
