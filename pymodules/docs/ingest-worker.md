# 文档导入服务（apps/ingest-worker）

> 返回总文档：[README.md](../README.md)

## 1. 职责与定位

`ingest-worker` 是知识库的数据入口，负责把 PDF / Markdown 文档解析、清洗、切片、商品名识别、向量化并写入 Milvus。整个导入链路由 LangGraph 编排，HTTP 层只负责接收上传与查询进度，重活全部在后台任务中执行。

- 服务注册名：`svc-ingest`
- Python 包名：`ingest_worker`
- distribution 名：`svc-ingest`
- 默认端口：`8001`
- 独立部署：是（自带 Dockerfile）

## 2. 目录结构

```
apps/ingest-worker/
├── Dockerfile
├── pyproject.toml
└── ingest_worker/
    ├── api/
    │   ├── app.py              # 应用入口：配置引导、生命周期、路由装配
    │   ├── dependencies.py     # FastAPI 依赖：配置、TaskTracker、工作流、后台任务
    │   ├── import_service.py   # 上传落盘、MinIO 持久化、后台执行全流程
    │   └── routers/
    │       ├── tasks.py        # /health、/upload、/status/{task_id}
    │       └── pages.py        # /import.html 静态页面
    └── graph/
        ├── main.py             # KBImportWorkflow：LangGraph 图构建与执行入口
        ├── state.py            # ImportGraphState 状态定义
        └── nodes/              # a~g 七个处理节点
```

## 3. 启动与配置

### 3.1 启动

```bash
# 统一虚拟环境（仓库根目录）
uv sync --all-packages --group cpu   # CPU 环境；GPU 用 --group gpu

cd /home/aipaas/aipaas/pymodules
uv run python -m ingest_worker.api.app
```

入口文件 [app.py](../apps/ingest-worker/ingest_worker/api/app.py) 先执行 `load_settings("ingest_worker")`，再导入业务模块。因此**配置加载必须先于业务导入**，这也是官方启动方式使用 `python -m` 而不是 `uvicorn ...:app` 的原因（两者都会触发模块导入，但 `-m` 与代码中的配置引导保持一致）。

也可以显式指定端口：

```bash
uv run uvicorn ingest_worker.api.app:app --host 0.0.0.0 --port 8001
```

### 3.2 配置命名空间 `ingest_worker`

配置来自 `configs/aipaas_config.base.yaml` 与 `configs/aipaas_config.{APP_ENV}.yaml`，schema 见 [ingest_worker.py](../packages/common/aipaas_common/conf/schemas/ingest_worker.py)。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `host` | `0.0.0.0` | 监听地址，可被 `INGEST_WORKER_HOST` 覆盖 |
| `port` | `8001` | 监听端口，可被 `INGEST_WORKER_PORT` 覆盖 |
| `max_content_length` | `500` | 切片最大长度 |
| `min_content_length` | `200` | 短内容合并阈值 |
| `item_name_chunk_k` | `3` | 商品名识别使用的切片数量 |
| `item_name_chunk_size` | `2500` | 商品名识别使用的切片内容长度 |
| `image_extensions` | `.jpg/.jpeg/.png/.gif/.bmp/.webp` | 参与图片理解的扩展名 |

依赖的 `common` 命名空间（LLM、Milvus、MinIO、MinerU、logging 等）见总文档「配置说明」。

### 3.3 环境变量

| 变量 | 用途 |
|---|---|
| `APP_ENV` | 指定覆盖层 `aipaas_config.{APP_ENV}.yaml` |
| `CONFIG_DIR` | ��置目录，默认定位到 `pymodules/configs` |
| `INGEST_WORKER_HOST` / `INGEST_WORKER_PORT` | 覆盖监听地址/端口 |
| `OPENAI_API_KEY` | LLM / 多模态 / MCP 通用密钥 |
| `MINERU_API_TOKEN` | MinerU PDF 解析服务令牌 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 凭据 |
| `NACOS_ENABLED` 等 | Nacos 注册开关与连接参数 |

## 4. HTTP 接口

路由无统一前缀，直接挂在应用根路径。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查，返回 `{"status":"ok","service":"svc-ingest"}` |
| POST | `/upload` | 多文件上传（`multipart/form-data`，字段名 `files`），每个文件返回一个 TaskID |
| GET | `/status/{task_id}` | 查询任务全局状态与节点进度 |
| GET | `/import.html` | 返回导入前端页面；文件不存在时 404 |

`/upload` 响应：

```json
{
  "code": 200,
  "message": " 文件上传成功, total: 1",
  "task_ids": ["6f1c..."]
}
```

`/status/{task_id}` 响应：

```json
{
  "code": 200,
  "task_id": "6f1c...",
  "status": "processing",
  "done_list": ["文件上传", "PDF转Markdown"],
  "running_list": ["文档切片"]
}
```

节点中文名由 `aipaas_common.log_decorator` 在节点执行前后自动维护，状态取值为 `pending / processing / completed / failed`。

## 5. 处理流程（LangGraph）

### 5.1 图结构

```
node_entry
   ├─(PDF)→ node_pdf_to_md → node_md_img → node_document_split
   └─(MD) ─────────────────→ node_md_img → node_document_split
                                              ↓
                                   node_item_name_recognition
                                              ↓
                                     node_bge_embedding
                                              ↓
                                      node_import_milvus → END
```

- 入口条件路由 `route_after_entry`：`is_pdf_read_enabled` 走 `node_pdf_to_md`，`is_md_read_enabled` 走 `node_md_img`，都不满足直接结束。
- 图在进程内懒加载并编译一次（`get_import_workflow` 使用 `lru_cache`），多请求复用同一编译结果。
- `run()` 同步入口兼容 CLI/测试；`arun()` 是 FastAPI 链路首选。

### 5.2 状态字段

状态定义见 [state.py](../apps/ingest-worker/ingest_worker/graph/state.py)，`total=False`，节点只更新自己关心的字段。

| 分组 | 字段 |
|---|---|
| 任务标识 | `task_id` |
| 控制标志 | `is_md_read_enabled`、`is_pdf_read_enabled` |
| 路径 | `import_file_path`、`file_dir`、`pdf_path`、`md_path` |
| 文件信息 | `file_title`、`item_name` |
| 中间数据 | `md_content`、`chunks` |

### 5.3 节点明细

| 节点 | 输入 | 主要动作 | 输出 |
|---|---|---|---|
| `node_entry` | `import_file_path` | 判断 `.pdf` / `.md`，提取 `file_title` | `is_pdf_read_enabled/pdf_path` 或 `is_md_read_enabled/md_path`、`file_title` |
| `node_pdf_to_md` | `pdf_path`、`file_dir` | 调用 MinerU 上传 PDF、轮询解析结果、下载 zip 并解压 | `md_path`、`md_content` |
| `node_md_img` | `md_content`、`md_path` | 扫描 MD 中引用的图片，多模态 LLM 生成图片描述，上传 MinIO，替换图片链接并备份新 MD | 更新 `md_content`、`md_path` |
| `node_document_split` | `md_content`、`file_title` | 按 MD 标题初切 → 无标题兜底 → 长切短合 → 统计 → JSON 备份 | `chunks` |
| `node_item_name_recognition` | `task_id`、`file_title`、`chunks` | 拼上下文调 LLM 识别商品名，回填切片，生成稠密/稀疏向量并写入 `kb_item_names` | `item_name`、更新 `chunks` |
| `node_bge_embedding` | `chunks` | 校验切片，分批调用向量服务，为切片补充 `dense_vector/sparse_vector` | 更新 `chunks` |
| `node_import_milvus` | `chunks` | 校验向量维度、自动建集合/索引、按 `file_title` 幂等清理旧数据、批量插入并回填 `chunk_id` | 更新 `chunks` |

补充说明：

- `node_md_img` 没有图片目录或 MD 未引用图片时直接跳过，不视为失败。
- `node_item_name_recognition` 与 `node_bge_embedding` 的向量生成都走 `aipaas_model_clients.lm.embedding_http_utils`，即远端 `model-embedding-worker`，导入服务本身不加载 BGE-M3。
- `node_import_milvus` 使用 `common.milvus.chunks_collection`（默认 `ku_chunks`），以 `file_title` 做幂等覆盖。

## 6. 关键实现

### 6.1 上传落盘与 MinIO

[import_service.py](../apps/ingest-worker/ingest_worker/api/import_service.py)：

1. 每个文件生成 UUID `task_id`，落盘到 `PROJECT_ROOT/output_files/YYYYMMDD/{task_id}/{filename}`。
2. 以 1MB 分块写盘，避免大文件占满内存。
3. 同步上传 MinIO：`pdf_files/YYYYMMDD/{filename}`。**MinIO 失败只告警、不阻断**后续本地处理。
4. 任务追踪先将 `upload_file` 标为 running，完成后标为 done。
5. 调用 `BackgroundTaskManager.spawn` 启动 `_run_graph`，上传请求立即返回。
6. `_run_graph` 调用 `workflow.arun(init_state, stream=True)` 拿到事件列表，逐条把已执行节点写入 `TaskTracker`；成功置为 `completed`；`BusinessError` 视为可预期失败，只记录修复提示，其他异常记录堆栈，均置为 `failed`。

### 6.2 依赖注入

[dependencies.py](../apps/ingest-worker/ingest_worker/api/dependencies.py) 集中提供：

- `get_ingest_settings`：返回已加载配置。
- `get_import_workflow`：进程内单例工作流。
- `get_import_service`：上传/状态查询共用的服务单例。
- `background_tasks`：进程级后台任务管理器，在 lifespan `shutdown` 阶段统一收尾。

### 6.3 生命周期

- 启用 Nacos（`common.nacos.enabled=true`）时注册 `svc-ingest`，并上报 host/port 元数据；未启用时不会导入 Nacos SDK。
- 关闭时通过 `AppLifespan` 触发 `background_tasks.aclose()`。

## 7. 构建与部署

Dockerfile 见 [Dockerfile](../apps/ingest-worker/Dockerfile)，采用两阶段构建：

1. **builder**：复制 `pyproject.toml` / `uv.lock` 后 `uv sync --frozen --no-dev --no-install-workspace --all-packages`，仅安装第三方依赖；再对 `packages/common`、`model-clients`、`nacos`、`retrieval`、`storage` 和 `apps/ingest-worker` 做 editable install。这样改源码不会击穿依赖层缓存。
2. **runtime**：复制 `.venv` 与源码、`configs`，暴露 `8001`，健康检查 `GET /health`，启动命令 `.venv/bin/python -m ingest_worker.api.app`。

```bash
cd /home/aipaas/aipaas/pymodules
docker build -f apps/ingest-worker/Dockerfile -t aipaas/svc-ingest:latest .

docker run -d --name svc-ingest -p 8001:8001 \
  -v /host/path/logs:/app/logs \
  -v /host/path/aipaas-configs:/app/configs:ro \
  -e APP_ENV=devcompany -e CONFIG_DIR=/app/configs \
  aipaas/svc-ingest:latest
```

- 数据卷：`/app/logs`、`/app/models`
- 环境变量：`PROJECT_ROOT=/app`、`MODELSCOPE_CACHE`、`HF_HOME`
- 日志文件：`ingest-worker_YYYYMMDD.log`（按模块名推导，下划线转连字符）

## 8. 依赖关系

```
ingest-worker
├── aipaas-common
├── aipaas-model-clients
├── aipaas-nacos
├── aipaas-retrieval
└── aipaas-storage
```

第三方主要依赖：FastAPI、Uvicorn、python-multipart、LangGraph、LangChain、langchain-text-splitters、modelscope、transformers、numpy。`torch` / `flagembedding` 不在此声明，必须由根 workspace 的 `cpu/gpu` 分组提供，避免与分组索引冲突。

## 9. 注意事项

- **任务状态在进程内**：`TaskTracker` 与后台任务都基于内存，多副本部署时 `/status/{task_id}` 可能打到没有该任务的实例。需要横向扩容时应改为共享存储（如 Redis）或保证会话粘滞。
- **`/import.html` 依赖 `PROJECT_ROOT/pages/import.html`**：仓库中未包含 `pages/` 时该接口返回 404，生产镜像需额外挂载或复制页面。
- **MinIO 与 Milvus 可用性**：MinIO 失败可降级，Milvus 失败会导致导入任务 `failed`。
- **端口一致性**：默认端口为 `8001`；前端反向代理（如 `/import-api/**`）与容器映射需与该端口保持一致。
- **重复导入**：以 `file_title` 为幂等键删除旧切片，修改文件名会被视为一份新文档。
