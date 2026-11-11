# 配置管理改造 Spec（已确认方案固化）

状态：待评审 · 决策依据：用户已确认的 16 条决策 + 全库扫描实证
范围：pymodules 下 4 个 worker（ingest/query/model-embedding/model-reranker）+ packages；不动 `apps/apis`、`mcp-agents2.0`（均不在 workspace）、`deploy` 中间件编排变量、prompts。

## 0. 需评审的判断点（扫码实证后得出，确认后才执行）

| # | 判断点 | 取值 | 依据 |
|---|--------|------|------|
| J1 | `query_mid_threshold` 默认值 | **0.55** | 代码中 mid 独立默认 0.55，且所有 env 均未设 `QUERY_HIGH_LIKE`，真实生效值=0.55（非 high 的 0.65） |
| J2 | base.yaml 连接默认值采用**容器拓扑**（`milvus:19530`/`minio:9000`/`/app/models/*`），devhome/devcompany 用 IP+本地路径覆盖 | 是 | 默认值优先级：Dockerfile ENV > 代码默认 > .env-参考；base 随镜像交付 |
| J3 | devhome.yaml 的模型路径/集合名按 devcompany 同构生成（仅替换主机为 11.0.1.190） | 是，标注运行时验证 | devhome 无 Python 侧 env 实证，仅有 Java 编排文件 |
| J4 | ingest 未引用配置项**删除**：`img_content_length`/`overlap_sentences`/`embedding_dim`/`embedding_batch_size`/`requests_per_minute`/`default_model`/`item_model`/`openai_api_base`/`vl_model`（后四个与 common 重复或零引用） | 删除 | grep 全库零引用；`apply_api_rate_limit(max_requests=10)` 为硬编码 |
| J5 | `lm_config.mcp_base_url`（`MCP_SERVER_BASE_URL`）零引用删除；`mcp_client.py` 硬编码 URL 改读配置；修 `mcp_config.bailian_api_key`（不存在的属性）→ `api_key` | 修复 | 实证 bug |
| J6 | `load_settings()` 带副作用：校验通过后自动调用 `apply_log_config()` 配置日志；`model_service.py`/`online_service.py` 的 import 期 `Semaphore` 改惰性创建 | 是 | 消除 import 顺序脆弱性 |

## 1. 部署侧配置包（pymodules/configs/，镜像内 COPY，部署时外部映射覆盖）

```
pymodules/configs/
├── aipaas_config.base.yaml            # 新增：全量默认，common + 4 模块命名空间
├── aipaas_config.devhome.yaml         # 新增：仅差异覆盖（11.0.1.190）
├── aipaas_config.devcompany.yaml      # 新增：仅差异覆盖（192.168.222.188）
├── aipaas_config.yaml                 # 删除（仅空 kubernetes 节点，废弃）
├── admin.conf / pipelines/ / prompts/ # 不动
```

合并规则：`base → {APP_ENV}` 两层，`APP_ENV ∈ {devhome, devcompany}` 强制校验，两文件都必须存在。首版不实现 `modules/` 覆盖层。

### base.yaml 结构（值按 J2 容器拓扑）

```yaml
common:
  llm:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key: ${oc.env:OPENAI_API_KEY,null}
    llm_model: qwen-flash
    llm_temperature: 0.1
    vl_model: qwen3.6-flash
  milvus:
    url: http://milvus:19530
    chunks_collection: chunks
    item_name_collection: kb_item_names
    entity_name_collection: kb_graph_entity_names
  minio:
    endpoint: minio:9000            # host:port，不带 scheme（决策8）
    secure: false
    bucket_name: aipaas
    img_dir: upload-images
    access_key: ${oc.env:MINIO_ACCESS_KEY,null}
    secret_key: ${oc.env:MINIO_SECRET_KEY,null}
  mongo:
    url: ${oc.env:MONGO_URL,null}
    db_name: aipaas
  mineru:
    base_url: https://mineru.net/api/v4   # 代码默认；.env-参考的 /api 为陈旧值
    api_token: ${oc.env:MINERU_API_TOKEN,null}
  mcp:
    mcp_base_url: https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp  # 字面值（决策10）
    api_key: ${oc.env:OPENAI_API_KEY,null}
  embedding_service: {url: http://svc-model-embedding:8101, timeout: 120}
  rerank_service:    {url: http://svc-model-reranker:8102,  timeout: 60}
  logging:
    console_enable: true
    console_level: INFO
    file_enable: true
    file_level: INFO
    file_retention: "7 days"
    file_prefix: null        # null → module_name 下划线转连字符推导（决策16）

ingest_worker:
  max_content_length: 500
  min_content_length: 200
  item_name_chunk_k: 3
  item_name_chunk_size: 2500
  image_extensions: [.jpg, .jpeg, .png, .gif, .bmp, .webp]

query_worker:
  query_high_threshold: 0.65
  query_mid_threshold: 0.55          # J1
  rerank_max_topk: 10
  rerank_min_topk: 3
  rerank_gap_abs: 0.5
  rerank_gap_ratio: 0.25
  max_context_chars: 12000

model_embedding_worker:
  backend: offline
  host: 0.0.0.0
  port: 8101
  max_batch_size: 64
  max_concurrency: 1
  offline:
    model_path: /app/models/bge-m3
    device: null                     # null → 代码侧 torch 探测（保留现有逻辑）
    use_fp16: true                   # Dockerfile ENV 优先
    normalize_embeddings: true
  online:
    api_key: ${oc.env:EMBEDDING_ONLINE_API_KEY,${oc.env:OPENAI_API_KEY,null}}  # 嵌套语法实测，失败则代码回退
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    model: text-embedding-v4
    dimension: 1536
    timeout: 60

model_reranker_worker:
  backend: offline
  host: 0.0.0.0
  port: 8102
  max_documents: 128
  max_concurrency: 1
  offline:
    model_path: /app/models/bge-reranker-large
    device: null
    use_fp16: true                   # Dockerfile BGE_RERANKER_FP16=1
    normalize: true
  online:
    api_key: ${oc.env:TEXT_RERANK_API_KEY,${oc.env:OPENAI_API_KEY,null}}
    model: qwen3-rerank
    instruct: 针对给定的查询，检索能够解答该查询的相关段落
```

### devcompany.yaml（全量）；devhome.yaml 同构替换 `192.168.222.188 → 11.0.1.190`（J3）

```yaml
common:
  milvus:
    url: http://192.168.222.188:19530
    chunks_collection: kb_chunks
  minio:
    endpoint: 192.168.222.188:9000
    bucket_name: knowledge-base-files
  mongo:
    db_name: kb002
  embedding_service: {url: http://127.0.0.1:8101}
  rerank_service:    {url: http://127.0.0.1:8102}
model_embedding_worker:
  offline: {model_path: D:/aigc/models/BAAI/bge-m3, device: cpu, use_fp16: false}
model_reranker_worker:
  offline: {model_path: D:/aigc/models/BAAI/bge-reranker-large, device: cpu, use_fp16: false}
```

## 2. 工程侧加载器（packages/common/aipaas_common/conf/）

```
conf/
├── __init__.py              # re-export load_settings / get_settings / SettingsError
├── settings.py              # 加载器（新增）
├── schemas/
│   ├── __init__.py          # MODULE_SCHEMAS 注册表
│   ├── common.py            # CommonSettings（llm/milvus/minio/mongo/mineru/mcp/embedding_service/rerank_service/logging）
│   ├── ingest_worker.py     # IngestWorkerSettings + Settings 组装模型
│   ├── query_worker.py
│   ├── model_embedding_worker.py
│   └── model_reranker_worker.py
└── （删除）app_config.py / minio_config.py / mineru_config.py / milvus_config.py / lm_config.py / bailian_mcp_config.py
```

`load_settings(module_name: str)` 流程：
1. `load_dotenv()`（本地开发读 pymodules/.env；`override=False`，真实环境变量优先）
2. `module_name` 必须在注册表 4 个 Python 包名内，否则 `SettingsError`
3. `CONFIG_DIR` 从 `os.environ` 强制读取，缺失即报错（无代码默认值）
4. `APP_ENV` 强制校验 ∈ {devhome, devcompany}
5. 加载 base + `{APP_ENV}` yaml（缺失任一即报错），`OmegaConf.merge`（**不 resolve**）
6. **先裁切**：`merged[module_name]` 缺失即报错；重组 `{common: merged.common, module: merged[module_name]}`
7. **后 resolve**：仅对裁切结果 `OmegaConf.resolve` → 无关模块缺失密钥不影响本进程
8. `to_container(resolve=True)` → Pydantic 校验（`extra="forbid"`；密钥字段 `Optional[SecretStr]`，消费方运行时校验非空）
9. 按进程缓存；同进程以不同 module_name 再调用 → 报错
10. 副作用（J6）：用 `common.logging` + `file_prefix or module_name.replace("_","-")` 调 `apply_log_config()`；启动日志打印裁切后键集合，SecretStr 掩码

配套：`get_settings()` 返回进程缓存（未加载则报错），供 packages 无 module_name 感知地使用。

依赖变更：`packages/common/pyproject.toml` 增加 `pydantic>=2`。

## 3. 下游改动点（精确到文件）

### P0 敏感/共享配置切换（packages）
| 文件 | 改动 |
|---|---|
| `storage/aipaas_storage/minio_utils.py` | `minio_config` → `get_settings().common.minio`；SecretStr 取 `get_secret_value()` |
| `storage/aipaas_storage/mongo_history_utils.py` | 删除 import 期实例化（纯惰性）；`MONGO_URL/DB_NAME` → `common.mongo` |
| `retrieval/aipaas_retrieval/milvus_utils.py` | `milvus_config` → `common.milvus` |
| `llm/aipaas_llm/lm/lm_utils.py` | `lm_config` → `common.llm` |
| `llm/aipaas_llm/lm/llm_client.py` | 同上 |
| `llm/aipaas_llm/lm/embedding_http_utils.py` | env → `common.embedding_service` |
| `llm/aipaas_llm/lm/reranker_http_utils.py` | env → `common.rerank_service` |
| `mcp-agents1.0/aipaas_mcp_agents/mcp_client.py` | 硬编码 URL → `common.mcp.mcp_base_url`；`bailian_api_key` → `api_key`（J5） |
| `common/aipaas_common/core/log.py` | 移除 import 期 env 读取；`apply_log_config(cfg, prefix)`；默认仅控制台兜底 |
| `common/aipaas_common/utils/path_util.py` | 移除 `load_dotenv` 副作用（dotenv 归 settings 统一加载）；PROJECT_ROOT 逻辑不变 |

### P1 死代码删除（删前逐文件 grep 复核引用）
`conf/app_config.py`、`storage/mongo_client.py`（已损坏）、`storage/mysql_client_manager.py`、`retrieval/{qdrant,es,neo4j}_client_manager.py`、`llm/embedding_client_manager.py`、`pymodules/configs/aipaas_config.yaml`、`pymodules/.env-参考`

### P2 模块 config.py 删除与引用点改造
- **ingest**：删 `ingest_worker/config.py`；`api/service.py` 顶部首先 `settings = load_settings("ingest_worker")`（在导入业务模块前）；`graph/main.py` 同样 bootstrap；节点 `b_node_pdf_to_md`（mineru）、`c_node_md_img`（`config.image_extensions`→`get_settings().module.*`；MinIO URL 按 `secure` 推导 scheme，修硬编码 `http://`；minio/lm 引用切换）、`d_node_document_split`、`e_node_item_name_recognition`、`g_node_import_milvus`
- **query**：删 `query_worker/config.py`；`api/service.py`、`graph/main_v1.py`、`main_v2.py` bootstrap `load_settings("query_worker")`；节点 `a/b/c/d/f/g` 切换（含 `query_config.*` → `get_settings().module.*`）
- **model-embedding / model-reranker**：删各自 `config.py`；`api/service.py` bootstrap；`service.py`/`model_service.py`/`online_service.py` 切换 + Semaphore 惰性化 + `device=None` 时保留 torch 探测（代码侧）

### P3 部署与 env 清理
- 4 个 Dockerfile：embedding/reranker 补 `COPY configs ./configs`；移除 `LOG_*` ENV（日志归 YAML）；不设 `CONFIG_DIR`（决策5，默认只在 compose/.env.example）
- `deploy/docker/docker-compose.yaml`：4 个 Python 服务加 `APP_ENV`/`CONFIG_DIR=/app/configs` + 卷 `${CONFIG_PACKAGE_DIR:-../../pymodules/configs}:/app/configs:ro`；移除已入 YAML 的 environment 键（MILVUS_URL/CHUNKS_COLLECTION/MINIO_BUCKET_NAME/MONGO_DB_NAME/OPENAI_BASE_URL/MODEL/EMBEDDING|RERANKER_SERVICE_URL/LOG_* 等）；保留密钥映射
- `deploy/docker/.env.example`：加 `APP_ENV=devcompany`/`CONFIG_DIR=/app/configs`/`MINERU_API_TOKEN`/`EMBEDDING_ONLINE_API_KEY`(可选)/`TEXT_RERANK_API_KEY`(可选)；精简已迁移的 Python 应用变量；中间件/profile/卷变量不动（决策9）
- `pymodules/.env` 精简为：`APP_ENV`/`CONFIG_DIR`/`OPENAI_API_KEY`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`/`MONGO_URL`/`MINERU_API_TOKEN`（+2 可选）；新建 `pymodules/.env.example` 同构
- 检查并修正根 `.gitignore`、`pymodules/.dockerignore`：排除 `.env*`（保留 `*.example`），确保 configs/*.yaml 可入镜像与仓库

## 4. 验证

1. `packages/common/tests/test_settings.py`（unittest，tmp CONFIG_DIR fixtures）：四模块 happy path、APP_ENV 非法、CONFIG_DIR 缺失、未注册 module、extra 键 forbid、`${oc.env:}` 插值、裁切隔离（他模块密钥缺失不报错）、同进程换名报错；`uv run python -m unittest` 全绿
2. 冒烟：`APP_ENV=devcompany CONFIG_DIR=pymodules/configs` + 密钥 dummy 值，四模块 `load_settings` 打印键集合
3. `uv sync --all-packages --group cpu` 后 `python -m compileall` 全部改动包；ingest/query 的 `api.service` import 冒烟（若现有 venv 依赖齐全）
4. 每次删除死代码前 grep 复核结果记录在案
