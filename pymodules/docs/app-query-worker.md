# 知识库查询服务（apps/app-query-worker）

> 返回总文档：[README.md](../README.md)

## 1. 职责与定位

`app-query-worker` 面向终端用户，接收自然语言问题，完成商品名确认、知识库混合检索、HyDE 检索、联网搜索、RRF 融合、重排、答案生成，并以 SSE 或一次性响应的形式返回结果。会话历史持久化在 MongoDB。

- 服务注册名：`app-query-worker`
- Python 包名：`app_query_worker`
- distribution 名：`app-query-worker`
- 默认端口：`8002`
- 独立部署：是（自带 Dockerfile）

## 2. 目录结构

```
apps/app-query-worker/
├── Dockerfile
├── pyproject.toml
└── app_query_worker/
    ├── api/
    │   ├── app.py              # 应用入口：配置引导、生命周期、路由装配
    │   ├── dependencies.py     # 配置、TaskTracker、SSEHub、工作流、后台任务
    │   ├── query_service.py    # 图执行 + SSE 事件推送
    │   └── routers/
    │       ├── query.py        # /health、/query
    │       ├── stream.py       # /stream/{session_id}
    │       └── history.py      # /history/{session_id}
    └── graph/
        ├── main_v2.py          # KBQueryWorkflowV2（服务实际使用）
        ├── main_v1.py          # 早期版本，保留参考
        ├── state.py            # QueryGraphState 状态定义
        └── nodes/              # a~g 七个处理节点
```

## 3. 启动与配置

### 3.1 启动

```bash
uv sync --all-packages --group cpu

cd /home/aipaas/aipaas/pymodules
uv run python -m app_query_worker.api.app
```

入口文件 [app.py](../apps/app-query-worker/app_query_worker/api/app.py) 在导入业务模块前执行 `load_settings("app_query_worker")`。指定端口启动：

```bash
uv run uvicorn app_query_worker.api.app:app --host 0.0.0.0 --port 8002
```

### 3.2 配置命名空间 `app_query_worker`

配置 schema 见 [app_query_worker.py](../packages/common/aipaas_common/conf/schemas/app_query_worker.py)，默认值如下。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `host` | `0.0.0.0` | 监听地址，可被 `APP_QUERY_WORKER_HOST` 覆盖 |
| `port` | `8002` | 监听端口，可被 `APP_QUERY_WORKER_PORT` 覆盖 |
| `query_high_threshold` | `0.65` | 商品名高置信度阈值，达到即直接确认 |
| `query_mid_threshold` | `0.55` | 商品名候选阈值，介于中/高之间时向用户反问 |
| `rerank_max_topk` | `10` | 重排结果硬上限（<=10） |
| `rerank_min_topk` | `3` | 断崖截断的最少保留条数 |
| `rerank_gap_abs` | `0.5` | 断崖截断绝对分差阈值 |
| `rerank_gap_ratio` | `0.25` | 断崖截断相对分差阈值 |
| `max_context_chars` | `12000` | 答案生成的上下文预算 |

### 3.3 环境变量

| 变量 | 用途 |
|---|---|
| `APP_ENV` / `CONFIG_DIR` | 配置环境与配置目录 |
| `APP_QUERY_WORKER_HOST` / `APP_QUERY_WORKER_PORT` | 覆盖监听地址/端口 |
| `OPENAI_API_KEY` | LLM 与百炼 MCP 密钥 |
| `EMBEDDING_SERVICE_URL` | 向量服务地址，默认 `http://svc-model-embedding:8003` |
| `RERANKER_SERVICE_URL` | 重排服务地址，默认 `http://svc-model-reranker:8004` |
| `MONGO_URL` | MongoDB 连接串（含账密） |
| `NACOS_ENABLED` 等 | Nacos 注册开关与连接参数 |

## 4. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查，返回 `{"ok": true}` |
| POST | `/query` | 知识库查询，支持流式/非流式 |
| GET | `/stream/{session_id}` | 订阅会话 SSE 事件流 |
| GET | `/history/{session_id}?limit=50` | 查询会话历史 |
| DELETE | `/history/{session_id}` | 清空会话历史 |

### 4.1 POST /query

请求：

```json
{
  "query": "华为擎云B530如何使用？",
  "session_id": "可选，不传则自动生成",
  "is_stream": true
}
```

流式（`is_stream=true`）立即返回，客户端凭 `session_id` 订阅 `/stream/{session_id}`：

```json
{"message": "结果正在处理中...", "session_id": "xxx"}
```

非流式（`is_stream=false`）等待图执行完，返回：

```json
{
  "message": "处理完成！",
  "session_id": "xxx",
  "answer": "……",
  "done_list": ["商品名确认", "向量检索", "RRF融合", "重排序", "答案生成"]
}
```

### 4.2 SSE 事件

`GET /stream/{session_id}` 返回 `text/event-stream`，响应头关闭缓存与 Nginx 缓冲。事件类型定义在 `aipaas_common.utils.sse_utils.SSEEvent`：

| 事件 | 触发时机 | 数据 |
|---|---|---|
| `ready` | 订阅建立 | 会话就绪 |
| `progress` | 节点执行进度 | 节点信息 |
| `delta` | 答案流式增量 | 文本片段 |
| `final` | 全链路完成 | `answer`、`state`（固定 `completed`）、`image_urls` |
| `error` | 执行异常 | `error` |

### 4.3 历史接口

- `GET /history/{session_id}`：按最近消息返回，`items` 为 Mongo 文档列表；查询异常返回 500。
- `DELETE /history/{session_id}`：返回删除条数 `deleted_count`。

历史读写通过 `asyncio.to_thread` 包装同步 Mongo 客户端，避免阻塞事件循环。

## 5. 处理流程（LangGraph）

### 5.1 图结构（V2，服务实际使用）

```
node_item_name_confirm
   ├─(已有 answer：反问/拒答)→ node_answer_output
   └─(并行) node_search_embedding ─┐
             node_search_embedding_hyde ─┼→ node_rrf → node_rerank → node_answer_output → END
             node_web_search_mcp ────────┘
```

- 入口固定为 `node_item_name_confirm`。
- 条件路由 `_route_after_item_name_confirm`：state 中已有 `answer` 时直接进入答案输出，否则并行发起三路检索。
- 三路检索都汇聚到 `node_rrf`，由 LangGraph 的并行分支机制等待全部完成。
- 图在进程内单例编译复用（`get_query_workflow` 使用 `lru_cache`）。

`main_v1.py` 保留了早期用虚拟节点 `multi_search` / `join` 表达并行的实现，仅供对照，服务不再使用。

### 5.2 状态字段

状态定义见 [state.py](../apps/app-query-worker/app_query_worker/graph/state.py)。

| 分组 | 字段 |
|---|---|
| 会话 | `session_id`、`message_id` |
| 输入 | `original_query`、`is_stream` |
| 检索中间结果 | `embedding_chunks`、`hyde_embedding_chunks`、`web_search_docs` |
| 排序结果 | `rrf_chunks`、`reranked_docs` |
| 生成结果 | `prompt`、`answer`、`image_urls` |
| 辅助信息 | `item_names`、`rewritten_query`、`history` |

### 5.3 节点明细

| 节点 | 主要动作 |
|---|---|
| `node_item_name_confirm` | 校验 `session_id/original_query` → 读取近期历史 → 保存用户消息 → LLM 提取 `item_names` 与 `rewritten_query` → 向量化后在 `kb_item_names` 混合检索 → 按阈值对齐商品名 → 必要时写入反问/拒答 `answer` → 回写历史 |
| `node_search_embedding` | 对 `rewritten_query` 生成稠密+稀疏向量，在 `ku_chunks` 上混合检索；有 `item_names` 时加 `item_name in [...]` 过滤，取 Top10 |
| `node_search_embedding_hyde` | LLM 生成假设性文档，与查询拼接后向量化并混合检索 |
| `node_web_search_mcp` | 调用百炼 WebSearch MCP，把 `pages` 转为 `{title,url,snippet}`；失败或无结果返回空 |
| `node_rrf` | 对普通检索与 HyDE 检索结果做 Reciprocal Rank Fusion（权重均为 1.0，k=60），输出 `rrf_chunks` |
| `node_rerank` | 合并 RRF 结果与联网结果 → 调用重排服务打分 → 断崖截断 → 输出 `reranked_docs` |
| `node_answer_output` | 已有 `answer` 则直接推送；否则构建 Prompt（上下文预算 + 历史）→ LLM 流式/非流式生成 → 提取图片链接 → 写入 Mongo 历史 |

### 5.4 商品名对齐规则

`node_item_name_confirm` 对每个提取名取 Top5 混合检索结果（稠密/稀疏权重 0.8/0.2，分数归一化），然后：

1. 存在 `score > query_high_threshold`（默认 0.65）的结果：全部加入确认列表，并批量回填历史消息的 `item_names`。
2. 无高置信度、但存在 `query_mid_threshold <= score < query_high_threshold`（默认 0.55~0.65）的结果：取前 3 个作为候选，生成反问「您是想问以下哪个产品：A、B？请明确一下型号。」。
3. 两者都没有：返回「抱歉，未找到相关产品，请提供准确型号以便我为您查询。」。

### 5.5 重排与断崖截断

- 重排调用 `aipaas_model_clients.lm.reranker_http_utils.arerank_documents`，服务端为 `model-reranker-worker`。
- 截断位置在 `[rerank_min_topk-1, rerank_max_topk-2]` 区间内扫描相邻分数差，满足 `abs_gap >= rerank_gap_abs` 或 `rel_gap >= rerank_gap_ratio` 即截断，否则取满 `rerank_max_topk`。

## 6. 关键实现

### 6.1 查询服务与 SSE

[query_service.py](../apps/app-query-worker/app_query_worker/api/query_service.py)：

- `submit_stream`：先 `SSEHub.create(session_id)` 建队列，再用 `BackgroundTaskManager.spawn` 后台跑 `run(...)`，HTTP 立即返回。
- `run`：清理旧任务状态 → 置为 processing → 构造初始 state → `workflow.arun` → 置为 completed → 推送 `FINAL`（`answer`、`state="completed"`、`image_urls`）。
- 异常：置为 failed；流式时推送 `ERROR`。
- 节点内部通过 `push_to_session` 推送 `delta`，服务在执行结束后推送 `FINAL`；`TaskTracker` 记录节点完成情况供非流式 `done_list` 使用。
- 非流式分支不会把异常抛给路由：`run()` 内部捕获异常并置为 failed，响应仍为 `处理完成！`，但 `answer` 为空。调用方需要区分时应结合日志或改为流式订阅 `error` 事件。

### 6.2 依赖注入

[dependencies.py](../apps/app-query-worker/app_query_worker/api/dependencies.py) 提供 `get_query_settings`、`get_query_workflow`、`get_query_service`、全局 `background_tasks`，并在 lifespan `shutdown` 阶段统一收尾。

### 6.3 生命周期

- `common.nacos.enabled=true` 时注册 `app-query-worker`，上报 host/port 元数据。
- 未启用 Nacos 的环境不会导入 Nacos SDK。

## 7. 构建与部署

Dockerfile 见 [Dockerfile](../apps/app-query-worker/Dockerfile)，两阶段构建，与导入服务一致：

- builder 先 `uv sync --frozen --no-dev --no-install-workspace --all-packages`，再 editable install `packages/common`、`model-clients`、`nacos`、`retrieval`、`storage`、`mcp-agents1.0` 和 `apps/app-query-worker`。
- runtime 暴露 `8002`，健康检查 `/health`，启动命令 `.venv/bin/python -m app_query_worker.api.app`。

```bash
cd /home/aipaas/aipaas/pymodules
docker build -f apps/app-query-worker/Dockerfile -t aipaas/app-query-worker:latest .

docker run -d --name app-query-worker -p 8002:8002 \
  -v /host/path/logs:/app/logs \
  -v /host/path/aipaas-configs:/app/configs:ro \
  -e APP_ENV=devcompany -e CONFIG_DIR=/app/configs \
  aipaas/app-query-worker:latest
```

日志文件：`app-query-worker_YYYYMMDD.log`。

## 8. 依赖关系

```
app-query-worker
├── aipaas-common
├── aipaas-model-clients
├── aipaas-nacos
├── aipaas-retrieval
├── aipaas-storage
└── aipaas-mcp-agent1（mcp<2 + openai-agents<0.20.0）
```

第三方主要依赖：FastAPI、Uvicorn、LangGraph、LangChain、langchain-openai、dashscope、ragas、modelscope、transformers、numpy。`torch` / `flagembedding` 由根 workspace 的 `cpu/gpu` 分组提供。

## 9. 注意事项

- **SSE 与会话状态在进程内**：`SSEHub`、`TaskTracker` 都是内存实现。多副本时 `/query` 与 `/stream` 必须落在同一实例（会话粘滞），否则订阅不到 `delta/final`。
- **`session_id` 语义**：不传由服务端生成；同一 `session_id` 的历史会在下次查询时被读取并参与商品名确认。
- **MongoDB 必须可用**：历史读写失败会影响查询链路；`GET /history` 失败返回 500。
- **联网搜索可降级**：MCP 调用异常只记录日志，不阻断本地知识库检索。
- **商品名过滤**：若确认了 `item_names`，Milvus 检索会附加 `item_name in [...]` 条件，可能显著缩小召回范围。
- **V1 图未使用**：`main_v1.py` 仅为历史参考，修改查询逻辑请以 `main_v2.py` 为准。
