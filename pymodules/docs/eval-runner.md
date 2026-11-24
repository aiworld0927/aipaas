# RAG 评估程序（apps/eval-runner）

> 返回总文档：[README.md](../README.md)

## 1. 职责与定位

`eval-runner` 不是常驻服务，而是一个基于 [ragas](https://github.com/explodinggradients/ragas) 的离线评估脚本，用于对知识库查询链路做端到端质量评估：逐条问题跑完整 RAG 流程，再用五项指标给答案与检索上下文打分。

- 入口：`apps/eval-runner/eval.py`
- distribution 名：无（未纳入 uv workspace，无 `pyproject.toml`）
- 默认端口：无（脚本进程）
- 独立部署：否（批处理脚本，非服务）

## 2. 目录结构

```
apps/eval-runner/
├── eval.py              # 评估主程序（step_1 ~ step_7）
├── qa.csv               # 评估测试集（UTF-8 BOM，列：question、ground_truth）
├── eval_result.csv      # 评估结果输出
└── eval_result-1.csv    # 历史评估结果样例
```

## 3. 评估输入与输出

### 3.1 输入 `qa.csv`

UTF-8 BOM 编码，必须包含两列：

| 列 | 说明 |
|---|---|
| `question` | 测试问题，建议带产品前缀，如「HAK 180 烫金机：……」 |
| `ground_truth` | 参考答案 |

加载时会校验列头、剔除空值行。

### 3.2 输出 `eval_result.csv`

列顺序固定：

```
question, contexts, answer, ground_truth,
Faithfulness, Answer Relevancy, Context Precision, Context Recall, Answer Correctness
```

`contexts` 为 JSON 字符串（保留列表结构），文件编码为 UTF-8 BOM。

## 4. 评估流程

| 步骤 | 函数 | 动作 |
|---|---|---|
| step_1 | `step_1_load_qa_dataset` | 读取并校验 `qa.csv` |
| step_2 | `step_2_init_evaluators` | 初始化 ragas 的 LLM 评估器与嵌入评估器 |
| step_3 | `step_3_run_query_pipeline` | 构造查询状态，调用查询图同步执行 |
| step_4 | `step_4_extract_answer_and_contexts` | 从最终状态取 `answer` 与 `reranked_docs[].text` |
| step_5 | `step_5_build_evaluation_dataset` | 映射为 ragas 字段（`user_input/retrieved_contexts/response/reference`） |
| step_6 | `step_6_run_ragas_evaluation` | 计算五项指标 |
| step_7 | `step_7_save_result_csv` | 按固定列顺序写出 CSV |

### 4.1 评估指标

- `Faithfulness`
- `Answer Relevancy`
- `Context Precision`
- `Context Recall`
- `Answer Correctness`

### 4.2 运行参数

| 参数 | 值 | 说明 |
|---|---|---|
| `RAGAS_TIMEOUT` | 180 秒 | 单项指标超时 |
| `RAGAS_MAX_WORKERS` | 4 | 并发工作线程 |
| `RAGAS_MAX_RETRIES` | 3 | 失败重试次数 |

`raise_exceptions=False`，单个指标失败不会中断整体评估；单条问题执行失败也只会记录空答案/空上下文，由 ragas 记为 NaN。

### 4.3 会话隔离

每条问题使用独立 `session_id`：

```
eval_{YYYYmmddHHMMSS}_{序号:03d}
```

多次运行互不干扰，问题之间也不会污染历史。

## 5. 运行方式与当前状态

设计上的运行方式：

```bash
cd /home/aipaas/aipaas/pymodules
uv run python apps/eval-runner/eval.py
```

### 5.1 当前状态：待适配

脚本目前仍引用**旧工程包结构**，不能在新 monorepo 中直接运行：

```python
from app.agents.query_process.main_graph import query_app
from app.agents.query_process.state import create_default_query_state
from app.core.log import logger
from app.clients.lm.embedding_utils import generate_embeddings
from app.clients.lm.lm_utils import get_llm_client
```

新结构中对应实现为：

| 旧引用 | 新位置 |
|---|---|
| `app.core.log.logger` | `aipaas_common.core.log.logger` |
| `app.clients.lm.lm_utils.get_llm_client` | `aipaas_model_clients.lm.lm_utils.get_llm_client` |
| `app.clients.lm.embedding_utils.generate_embeddings` | `aipaas_model_clients.lm.embedding_http_utils.generate_embeddings`（异步为 `agenerate_embeddings`） |
| `app.agents.query_process.main_graph.query_app` | `app_query_worker.graph.main_v2.KBQueryWorkflowV2` |
| `app.agents.query_process.state.create_default_query_state` | `app_query_worker.graph.state.create_default_query_state` |

同时 `apps/eval-runner` 未纳入根 `pyproject.toml` 的 workspace，也没有独立 `pyproject.toml` / Dockerfile，因此依赖（ragas、pandas 等）需在适配时补齐。

### 5.2 已知实现问题

适配时需要一并处理：

1. **导入路径**：如上表，全部改为 `aipaas_*` / `app_query_worker`。
2. **同步/异步入口**：新查询图推荐 `asyncio.run(KBQueryWorkflowV2().arun(state, stream=False))`；当前脚本假定 `query_app.invoke` 同步可用。
3. **嵌入适配器方法签名**：`BgeM3EmbeddingsAdapter.embed_documents` / `embed_query` 缺少 `self`，作为实例方法被 ragas 调用时会因参数错位抛 `TypeError`，需要改为 `def embed_documents(self, texts)` 与 `def embed_query(self, text)`。
4. **嵌入客户端**：新实现通过 HTTP 调用 `model-embedding-worker`，评估前需保证该服务可用，或改为本地加载。
5. **兼容性 shim**：脚本在导入 ragas 前注入了 `langchain_community.chat_models.vertexai` 占位模块，解决 ragas 0.4.3 与 langchain-community 0.4.x 的兼容问题；适配后如仍使用同等版本组合，需要保留该处理。

## 6. 依赖与部署

- 无 Dockerfile、无 pyproject，不参与 `uv sync --all-packages`。
- 计划依赖：`ragas`、`pandas`、`langchain-core`，以及查询链路所需的 `app-query-worker`、`aipaas-common`、`aipaas-model-clients`。
- 评估会真实调用 LLM、嵌入服务、Milvus、MongoDB 与联网检索，属于**有外部副作用的集成测试**，建议在测试环境运行。

## 7. 注意事项

- `qa.csv` 与 `eval_result*.csv` 体积较小，可随代码库维护；评估结果是否入库由使用方决定。
- 指标依赖 LLM 打分，结果存在波动，比较不同版本时应使用同一测试集与同一评估模型。
- 当前脚本不能直接运行，修改前请先完成第 5.1、5.2 节的适配。
