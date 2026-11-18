# rag-ai-api（P0 契约骨架）

FastAPI 骨架服务，与 `aipaas-contracts/openapi/aipaas-openapi-v1.yaml` 使用同一 `/api/v1` 路径与
`{code,message,data,traceId}` 信封。

- `/healthz`、`/readyz`：200
- `/api/v1/system/info`：契约元信息
- 其他契约路由：501 `SYS.NOT_IMPLEMENTED`（骨架不实现业务逻辑）

## 本地运行

```bash
cd pymodules/apps/rag-ai-api
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn rag_ai_api.main:app --host 0.0.0.0 --port 8100
```

或使用仓库根目录 uv（无需修改 workspace lock）：

```bash
uv run --with fastapi --with uvicorn uvicorn rag_ai_api.main:app --app-dir pymodules/apps/rag-ai-api --port 8100
```

## 边界

- 该服务不加入 `pymodules` uv workspace 成员，避免 P0 骨架引入 torch 等重依赖；正式 AI 能力
  将按 P0 功能清单并入既有 workspace 包。
- P0 不实现混合检索、Cross-Encoder 重排、多轮会话；`rerank`/`kbIds` 等仅为契约预留字段。
