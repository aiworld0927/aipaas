# AIPaaS Python AI 服务开发部署文档

> 适用对象：后端开发 / 运维 / DevOps
> 代码位置：`/coding/aipaas/pymodules/`
> 架构：uv workspace（apps + packages）
> Python 版本：≥ 3.12,< 3.14
> 配置方式：`configs/aipaas_config.*.yaml`（`CONFIG_DIR` + `APP_ENV` 分层加载）

---

## 一、总览

本章先讲清"工程长什么样、有哪些部署单元、依赖怎么走"，后续各章都是对其中某一块的展开。

### 1.1 工程结构

AIPaaS 的 AI 服务层采用 **uv workspace** 单体仓（monorepo）结构，将可独立运行的服务放在 `apps/`，将可被复用的能力放在 `packages/`。所有 workspace 成员共用一个 `.venv`（位于 `pymodules/.venv`），通过 **editable install** 实现源码修改即时生效。

```
pymodules/
├── pyproject.toml          # workspace 根（不声明 dependencies；定义 cpu/gpu 依赖组）
├── uv.lock                 # 统一锁文件
├── .python-version         # Python 版本锁定
├── .env                    # 本地环境变量（APP_ENV / CONFIG_DIR / 密钥）
├── .dockerignore           # 排除 .venv / logs / tests / __pycache__
├── .venv/                  # 所有成员共用的虚拟环境
├── apps/                   # 可独立运行的应用（每个 app = 一个部署单元）
│   ├── ingest-worker/          # 文档导入服务（端口 8001）
│   ├── query-worker/           # 用户查询服务（端口 8002）
│   ├── model-embedding-worker/ # 嵌入模型推理服务（端口 8003）
│   ├── model-reranker-worker/  # 重排序模型推理服务（端口 8004）
│   ├── apis/                   # 管理端业务逻辑（不在 workspace）
│   └── eval-runner/            # RAG 评估脚本（不在 workspace）
├── packages/               # 共享能力包（被 apps 和 packages 向下依赖）
│   ├── common/             # 基础共享库（含统一配置加载 conf/settings.py）
│   ├── llm/                # LLM 接入层
│   ├── retrieval/          # 检索层（Milvus / ES / Qdrant / Neo4j）
│   ├── storage/            # 存储层（Mongo / MinIO / MySQL / K8s）
│   ├── pipeline/           # RAG 流程编排基类
│   ├── mcp-agents1.0/      # MCP 客户端（v1: mcp<2）
│   └── mcp-agents2.0/      # MCP 客户端（mcp≥2 版本，不在 workspace）
├── configs/                # 配置包（aipaas_config.base/devhome/devcompany.yaml、prompts）
└── tests/                  # 开发测试脚本（不纳入 workspace）
```

> **不在 workspace 中但存在于代码库的目录**：`apps/apis/`（管理端业务逻辑）、`apps/eval-runner/`（RAG 评估脚本）、`packages/mcp-agents2.0/`（mcp≥2 版本，暂未被任何成员依赖）。这些目录独立管理，不参与 `uv sync --all-packages`。

### 1.2 部署单元（`apps/`）

| App | 端口 | 职责 | 入口模块 | 配置命名空间 |
|-----|------|------|----------|--------------|
| `svc-ingest`（`apps/ingest-worker`） | 8001 | 接收文档上传 → LangGraph 流程编排（PDF转MD → 图片处理 → 文档切片 → 主体识别 → BGE 向量化 → 写入 Milvus） | `ingest_worker.api.service:app` | `ingest_worker` |
| `svc-query`（`apps/query-worker`） | 8002 | 接收用户查询 → LangGraph 流程编排（主体确认 → 向量检索 / HyDE 检索 / MCP Web 搜索 → RRF 融合 → 重排 → SSE 流式输出） | `query_worker.api.service:app` | `query_worker` |
| `svc-model-embedding`（`apps/model-embedding-worker`） | 8003 | 嵌入模型独立推理服务：离线 BGE-M3（dense + sparse）/ 在线 DashScope 兼容接口（仅 dense） | `model_embedding_worker.api.service:app` | `model_embedding_worker` |
| `svc-model-reranker`（`apps/model-reranker-worker`） | 8004 | 重排序模型独立推理服务：离线 bge-reranker-large / 在线 DashScope TextReRank | `model_reranker_worker.api.service:app` | `model_reranker_worker` |

### 1.3 共享能力（`packages/`）

| Package | Python 包名 | 职责 |
|---------|-------------|------|
| `packages/common` | `aipaas_common` | 基础共享：日志（loguru）、路径、SSE 工具、任务管理、配置管理（OmegaConf + Pydantic）、请求 ID 上下文 |
| `packages/llm` | `aipaas_llm` | LLM 接入：DashScope / Ollama / 百炼、LangChain 封装；嵌入与重排只保留 **HTTP 客户端**（`embedding_http_utils` / `reranker_http_utils`） |
| `packages/retrieval` | `aipaas_retrieval` | 检索层：Milvus、Elasticsearch、Qdrant、Neo4j 客户端 |
| `packages/storage` | `aipaas_storage` | 存储层：MongoDB（含历史记录）、MinIO、MySQL、Kubernetes 客户端 |
| `packages/pipeline` | `aipaas_pipeline` | RAG 流程编排基类 |
| `packages/mcp-agents1.0` | `aipaas_mcp_agents` | MCP 客户端（v1 版本：`mcp>=1.19.0,<2` + `openai-agents<0.20.0`） |

### 1.4 依赖方向（严格遵守，禁止反向）

```
apps（svc-ingest / svc-query）
  └─► packages
        ├─► aipaas-retrieval
        │     └─► aipaas-llm
        │           └─► aipaas-common
        ├─► aipaas-storage
        │     └─► aipaas-common
        ├─► aipaas-pipeline
        │     └─► aipaas-common
        └─► aipaas-mcp-agent1
              └─► aipaas-common
```

**核心约束**：
- `packages/` 内的包**禁止** import `apps/` 中的任何模块。
- 下层包（如 `common`）**禁止** import 上层包（如 `llm`、`retrieval`）。
- 新增依赖必须在被依赖方的 `pyproject.toml` 中声明，并通过 `[tool.uv.sources]` 指向 `{ workspace = true }`。

### 1.5 全文导航

| 章节 | 内容 | 何时查阅 |
|------|------|----------|
| 一、总览 | 工程结构、部署单元、共享包、依赖方向 | 新人上手 / 全局对照 |
| 二、开发环境搭建 | uv workspace 安装、IDE 解释器 | 首次搭建环境 |
| 三、开发模式启动服务 | 四个服务的启动命令、配置与环境变量 | 日常开发调试 |
| 四、依赖管理 | 第三方依赖、包间依赖、新增包、CPU/GPU 分组 | 改动依赖时 |
| 五、镜像构建与运行 | Dockerfile、单机运行、docker compose | 打包发布 / 联调 |
| 六、四个模块独立说明 | 各模块职责、快速启动、接口清单、curl、配置项 | 开发联调 / 接口对接 |
| 七、模型服务实现要点 | 双后端、异步实现、业务侧调用、排错 | 模型服务运维 |
| 八、日志与健康检查 | 日志配置、容器挂载、探针 | 运维观测 / 排查 |
| 九、扩展规范 | 新增共享包 / 应用包 | 功能扩展 |
| 十、测试与验证清单 | 编译、import、依赖方向、端点、镜像 | 提交前自检 / CI |
| 十一、常见问题 | 典型故障 | 遇到报错先查这里 |
| 附录 | 快速参考卡 | 常用命令速查 |

---

## 二、开发环境搭建

### 2.1 前置条件

- Python ≥ 3.12（仓库根 `.python-version` 已锁定）
- [uv](https://docs.astral.sh/uv/)（包管理器，替代 pip / poetry）
- IDE（推荐 PyCharm 或 VS Code）

### 2.2 安装 workspace 全部依赖

```bash
cd pymodules

# ⚠️ 必须加 --all-packages，且必须用 --group 二选一选定 PyTorch 构建！
# 裸 uv sync 只会装根项目（aipaas-monorepo），而根项目没有 dependencies，
# 结果就是一个空 .venv，import 全部报 ModuleNotFoundError。
# 不带 --group 则不会安装 torch / flagembedding / pymilvus[model]，模型服务无法运行。
uv sync --all-packages --group cpu    # CPU 机器
# uv sync --all-packages --group gpu  # NVIDIA GPU 机器（CUDA 12.8 构建）

# 校验锁文件是否与 pyproject.toml 一致（CI 也会跑）
uv lock --check

# 冒烟测试：import 全部 workspace 成员
uv run python -c "import aipaas_common, aipaas_llm, aipaas_retrieval, aipaas_storage, aipaas_pipeline, aipaas_mcp_agents, ingest_worker, query_worker; print('ok')"
```

### 2.3 IDE 解释器配置

IDE 解释器必须指向 **`pymodules/.venv/bin/python`**。

- **PyCharm**：Settings → Project → Python Interpreter → Add → Existing → `pymodules/.venv/bin/python`
- **VS Code**：`Ctrl+Shift+P` → "Python: Select Interpreter" → 选择 `pymodules/.venv/bin/python`

> 由于所有 workspace 成员都是 **editable install**，`packages/` 或 `apps/` 里的源码修改会在重启服务后即时生效，无需重新 `uv sync`。

### 2.4 辅助脚本

```bash
# 查看当前 workspace 成员
uv workspace list

# 锁定所有成员的依赖变更
uv lock && uv sync --all-packages --group cpu
```

---

## 三、开发模式启动服务

所有命令均在 `pymodules/` 目录下执行，共用 `.venv`。

### 3.1 启动顺序与命令

先起两个**模型服务**（8003 / 8004），再起两个**业务服务**（8001 / 8002）——业务服务通过 HTTP 调用模型服务。开发时统一加 `--reload`。

| 服务 | 端口 | 启动命令（在 `pymodules/` 下执行） |
|------|------|------|
| 嵌入模型 | 8003 | `uv run uvicorn model_embedding_worker.api.service:app --host 0.0.0.0 --port 8003 --reload` |
| 重排序模型 | 8004 | `uv run uvicorn model_reranker_worker.api.service:app --host 0.0.0.0 --port 8004 --reload` |
| 导入服务 | 8001 | `uv run uvicorn ingest_worker.api.service:app --host 0.0.0.0 --port 8001 --reload` |
| 查询服务 | 8002 | `uv run uvicorn query_worker.api.service:app --host 0.0.0.0 --port 8002 --reload` |

```bash
# 终端 1：嵌入模型
cd pymodules && uv run uvicorn model_embedding_worker.api.service:app --port 8003 --reload

# 终端 2：重排序模型
cd pymodules && uv run uvicorn model_reranker_worker.api.service:app --port 8004 --reload

# 终端 3：导入服务
cd pymodules && uv run uvicorn ingest_worker.api.service:app --port 8001 --reload

# 终端 4：查询服务
cd pymodules && uv run uvicorn query_worker.api.service:app --port 8002 --reload
```

`--reload` 会监听源码变更并自动重启，开发时务必加上。各模块的接口与配置细节见 [第六章](#六四个模块独立说明)。

### 3.2 限定 workspace 成员执行

```bash
# 只加载 svc-ingest 及其依赖，避免 query-worker 的重依赖拖慢启动
uv run --package svc-ingest python -m ingest_worker.graph.main

# 或执行 svc-query
uv run --package svc-query python -c "from query_worker.api.service import app; print(app.title)"
```

### 3.3 配置加载与环境变量

#### 配置分层

所有模块启动时由入口调用 `load_settings(<module_name>)`，合并顺序为 `base → APP_ENV 覆盖层`：

```
configs/
├── aipaas_config.base.yaml        # 全量默认值（容器 / compose 拓扑）
├── aipaas_config.devhome.yaml     # 家中开发环境覆盖（主机、集合名、模型路径）
└── aipaas_config.devcompany.yaml  # 公司开发环境覆盖
```

加载规则（[settings.py](file:///coding/aipaas/pymodules/packages/common/aipaas_common/conf/settings.py)）：
1. `load_dotenv()` 读取 `.env`（`override=False`，真实环境变量优先）。
2. 只保留 `common` + **本模块命名空间**，其他模块的缺失密钥不影响本进程。
3. 先裁切后解析 `${oc.env:...}`，YAML 不落明文密钥。
4. Pydantic 校验，`extra="forbid"`：写错字段名或漏字段直接启动失败。
5. 按进程缓存；同一进程不能换 `module_name` 再次加载。

#### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `CONFIG_DIR` | ✅ | 配置包目录。默认 `/home/aipaas/aipaas/pymodules/configs`，容器内为 `/app/configs`。 |
| `APP_ENV` | ✅ | `devhome` / `devcompany`（默认 `devhome`），决定加载哪个覆盖层。 |
| `OPENAI_API_KEY` | 视功能 | LLM / MCP / 在线嵌入与重排的默认密钥回退。 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 业务服务 | MinIO 凭证（YAML 中 `${oc.env:...}` 注入）。 |
| `MONGO_URL` | 业务服务 | MongoDB 连接串（含账密）。 |
| `MINERU_API_TOKEN` | ingest | MinerU 文档解析服务令牌。 |
| `EMBEDDING_ONLINE_API_KEY` | 在线嵌入 | 未设置时回退 `OPENAI_API_KEY`。 |
| `TEXT_RERANK_API_KEY` | 在线重排 | 未设置时回退 `OPENAI_API_KEY`。 |
| `PROJECT_ROOT` | 容器内 | 项目根目录；日志目录固定为 `PROJECT_ROOT/logs`，容器内为 `/app`。 |

> `.env` 文件可直接放在 `pymodules/` 下，启动时自动加载。LLM 地址、Milvus / MinIO 地址、集合名、模型路径等**业务配置都在 YAML 中**，不再通过 `MILVUS_URL` 之类的环境变量传入。

### 3.4 启动校验

```bash
curl -s http://localhost:8003/ready    # 嵌入模型：模型加载完成才返回 200
curl -s http://localhost:8004/ready    # 重排序模型
curl -s http://localhost:8001/health   # ingest-worker
curl -s http://localhost:8002/health   # query-worker
```

---

## 四、依赖管理

### 4.1 新增第三方依赖到某个 workspace 成员

```bash
# 为 aipaas-retrieval 新增 elasticsearch
uv add --package aipaas-retrieval elasticsearch>=8.0.0

# 从 aipaas-llm 移除 dashscope
uv remove --package aipaas-llm dashscope

# 变更后务必重新锁定 + 同步
uv lock && uv sync --all-packages --group cpu
```

### 4.2 workspace 内部新增依赖

如果一个成员要依赖另一个 workspace 成员，除了 `uv add` 之外，还需要确认被依赖方的 `pyproject.toml` 中有 `[tool.uv.sources]` 声明。uv 通常会自动处理，手动检查：

```toml
# 在依赖方的 pyproject.toml 中
[tool.uv.sources]
aipaas-common = { workspace = true }
aipaas-llm = { workspace = true }
```

### 4.3 新增包到 workspace

```bash
# 1. 建目录 + 写 pyproject.toml（参考 packages/common/pyproject.toml 模板）
mkdir -p packages/<new-pkg>/<new_pkg_name>
touch packages/<new-pkg>/<new_pkg_name>/__init__.py

# 2. 在根 pyproject.toml 的 [tool.uv.workspace].members 中追加路径
# 3. 重新锁文件并同步
uv lock && uv sync --all-packages --group cpu
```

### 4.4 GPU 版 PyTorch（cpu / gpu 分组二选一）

torch / torchvision / torchaudio / flagembedding / pymilvus[model] 统一放在根 `pyproject.toml` 的
**互斥分组** `cpu` 与 `gpu` 中（`conflicts` 声明两组不能同时启用），同步时二选一：

```bash
uv sync --all-packages --group cpu    # CPU 机器：torch 2.11.0+cpu（pytorch.org/whl/cpu）
uv sync --all-packages --group gpu    # GPU 机器：torch 2.11.0+cu128（pytorch.org/whl/cu128）
```

注意：
- 成员工程**不得**在自身 `dependencies` 里声明 torch / flagembedding / pymilvus[model]，
  否则其无条件传递依赖会让 torch 在分组外走默认 PyPI 源，与分组索引冲突
  （uv 报 `conflicting indexes for package 'torch' in all marker environments`）。
- 不带 `--group` 的裸同步不会安装这些包，模型服务无法运行。
- 本地开发机切换 GPU/CPU 的完整命令与排错见 [PyTorch_CPU_GPU版本切换.md](file:///d:/coding/gitee/aipaas/docs/学习笔记/项目部署/PyTorch_CPU_GPU版本切换.md)。
- 两个模型服务的 Dockerfile 用 **构建参数 `TORCH_VARIANT`** 决定镜像里装哪种 torch，无需手工改文件：

```dockerfile
ARG TORCH_VARIANT=gpu                                  # 默认 gpu，构建时 --build-arg 覆盖
RUN uv sync --package model-embedding-worker --no-dev --group cpu

# gpu 变体额外把 torch 系替换为 CUDA 12.8 构建；cpu 变体跳过
RUN if [ "${TORCH_VARIANT}" = "gpu" ]; then \
      uv pip install --python /app/.venv/bin/python --reinstall \
        --extra-index-url https://download.pytorch.org/whl/cu128 \
        torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128; \
    fi
```

构建完成后 Dockerfile 内会自校验：`cpu` 分组必须满足 `torch.version.cuda is None`，`gpu` 分组必须是 `CUDA 12.8`，不一致直接构建失败，避免传出“标签写 GPU、实际是 CPU”的镜像。

| `TORCH_VARIANT` | 安装来源 | 镜像体积 | 适用 |
|---|---|---|---|
| `gpu`（默认） | `pytorch.org/whl/cu128` | ≈12GB | 有 NVIDIA 显卡（运行时 `offline.device: cuda`） |
| `cpu` | `pytorch.org/whl/cpu` | ≈2GB | 无显卡 / 纯 CPU 推理（`offline.device: cpu`） |

---

## 五、镜像构建与运行

四个 app 各自拥有独立的 Dockerfile，采用 **多阶段构建**（builder 阶段装依赖 + runtime 阶段只拷贝 `.venv` 和源码），镜像体积小、构建可缓存。

### 5.1 构建镜像

```bash
# 必须在 pymodules/ 目录下执行，context 就是 pymodules/
cd pymodules

# 导入服务
docker build -f apps/ingest-worker/Dockerfile -t aipaas/svc-ingest:latest .

# 查询服务
docker build -f apps/query-worker/Dockerfile -t aipaas/svc-query:latest .

# 嵌入模型服务（端口 8003）—— 必须加 --network host，并按需选择 torch 构建
docker build --network host --build-arg TORCH_VARIANT=gpu \
  -f apps/model-embedding-worker/Dockerfile -t aipaas/svc-model-embedding:latest .

# 重排序模型服务（端口 8004）
docker build --network host --build-arg TORCH_VARIANT=gpu \
  -f apps/model-reranker-worker/Dockerfile -t aipaas/svc-model-reranker:latest .
```

两个模型服务的构建有两个必选项：

| 选项 | 作用 | 不加的后果 |
|---|---|---|
| `--network host` | 构建容器走宿主机网络 | docker 默认 bridge 网络访问 `download.pytorch.org` / `pypi.nvidia.cn` 会 `network unreachable` 或 `ReadTimeout`，`uv sync` 装 torch 失败 |
| `--build-arg TORCH_VARIANT=cpu\|gpu` | 选择 CPU / GPU 版 torch | 省略时默认 `gpu`（≈12GB，含 CUDA 12.8）；纯 CPU 机器要用 `cpu`（≈2GB） |

`TORCH_VARIANT` 也可在 compose 里统一配置，见 5.3 节。构建结束前 Dockerfile 会打印实际 torch 版本，可用下面命令复查：

```bash
docker run --rm aipaas/svc-model-embedding:latest \
  .venv/bin/python -c "import torch; print(torch.__version__, torch.version.cuda)"
# gpu: 2.11.0+cu128 12.8      cpu: 2.11.0+cpu None
```

构建细节（以 ingest-worker 为例）：
- **builder 阶段**：`python:3.12-slim` + `uv`，执行 `uv sync --package svc-ingest --no-dev`（仅装生产依赖）
- **runtime 阶段**：新的 `python:3.12-slim`，只拷贝 `.venv` + `packages/` + `apps/ingest-worker/` + `configs/` + `pyproject.toml` + `uv.lock`
- 环境变量内置了生产默认值：`PROJECT_ROOT=/app`、`MODELSCOPE_CACHE=/app/models/modelscope_cache` 等
- 声明了两个卷：`/app/logs`（日志）、`/app/models`（模型缓存）

### 5.1.1 镜像版本与重建策略（重要）

`docker-compose up -d` **默认不构建镜像**，判定规则：

| 场景 | 是否 build |
|---|---|
| 本地已存在 `image:` 指定的 tag（如 `aipaas/svc-model-embedding:latest`） | ❌ 直接用它起容器，源码改了也不会生效 |
| 本地不存在该 tag（首次、删了镜像、换了版本号） | ✅ 自动 build 一次 |
| `docker-compose up -d --build` | ✅ 强制重建 |
| `docker-compose build <service>` | ✅ 显式构建 |

即：`up -d` 只负责“启动”，不负责“更新”；`apps/*/` 下的代码没有以卷的方式挂进容器，必须重建镜像才能生效。

镜像版本由 `deploy/docker/.env` 的 `IMAGE_TAG` 统一控制（compose 中 `image: aipaas/svc-xxx:${IMAGE_TAG:-latest}`）：

```bash
# 开发迭代：保持 latest，改完代码显式重建再起
docker-compose build svc-model-embedding && docker-compose up -d svc-model-embedding

# 发布：用版本号打 tag，镜像可追溯、可回滚
IMAGE_TAG=0.1.0 docker-compose build
IMAGE_TAG=0.1.0 docker-compose up -d

# 回滚：切回旧 tag 重启即可（镜像还在本地）
IMAGE_TAG=0.0.9 docker-compose up -d

# 彻底重建（跳过所有层缓存，依赖也会重装，耗时最长）
docker-compose build --no-cache svc-query
```

排查“改了代码没生效”时，先比对镜像与源码时间：

```bash
docker image inspect aipaas/svc-query:latest --format '{{.Created}}'
find apps/query-worker -name "*.py" -newer /dev/null -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort -r | head -3
```

镜像时间早于源码时间 → 需要重建。注意模型服务的 `TORCH_VARIANT`（cpu/gpu）变化也会触发重建，见 5.3。

### 5.2 单独运行（需要中间件已就绪）

以下命令在 `pymodules/` 目录下执行。配置由 `configs/aipaas_config.*.yaml` 提供：`APP_ENV` 选择覆盖层，`CONFIG_DIR` 指向容器内配置目录。覆盖层里的中间件地址必须在宿主机可达；若想用 `docker-compose` 的容器名寻址，请直接用 5.3 的 compose 方式。

#### 5.2.1 业务服务

```bash
# 运行 svc-ingest（端口 8001）
docker run -d --name svc-ingest -p 8001:8001 \
  --network host \
  -e APP_ENV=devhome -e CONFIG_DIR=/app/configs \
  -e OPENAI_API_KEY=sk-xxx \
  -e MINIO_ACCESS_KEY=minioadmin -e MINIO_SECRET_KEY=minioadmin \
  -e MONGO_URL='mongodb://admin:123456@127.0.0.1:27017/?authSource=admin' \
  -e MINERU_API_TOKEN=xxx \
  -v $(pwd)/configs:/app/configs:ro \
  -v /tmp/logs/svc-ingest:/app/logs \
  aipaas/svc-ingest:latest

# 运行 svc-query（端口 8002）
docker run -d --name svc-query -p 8002:8002 \
  --network host \
  -e APP_ENV=devhome -e CONFIG_DIR=/app/configs \
  -e OPENAI_API_KEY=sk-xxx \
  -e MINIO_ACCESS_KEY=minioadmin -e MINIO_SECRET_KEY=minioadmin \
  -e MONGO_URL='mongodb://admin:123456@127.0.0.1:27017/?authSource=admin' \
  -v $(pwd)/configs:/app/configs:ro \
  -v /tmp/logs/svc-query:/app/logs \
  aipaas/svc-query:latest
```

#### 5.2.2 模型服务

模型服务必须把**离线模型目录**挂载到配置中 `offline.model_path` 指向的路径（base 默认为 `/app/models/bge-m3`、`/app/models/bge-reranker-large`；`devhome` / `devcompany` 覆盖层会改成本机的模型目录，挂载路径要与之保持一致）。

```bash
# 运行 svc-model-embedding（端口 8003）
docker run -d --name svc-model-embedding -p 8003:8003 \
  --network host \
  -e APP_ENV=devhome -e CONFIG_DIR=/app/configs \
  -e OPENAI_API_KEY=sk-xxx \
  -v $(pwd)/configs:/app/configs:ro \
  -v /models/bge-m3:/app/models/bge-m3:ro \
  -v /tmp/logs/svc-model-embedding:/app/logs \
  aipaas/svc-model-embedding:latest

# 运行 svc-model-reranker（端口 8004）
docker run -d --name svc-model-reranker -p 8004:8004 \
  --network host \
  -e APP_ENV=devhome -e CONFIG_DIR=/app/configs \
  -e OPENAI_API_KEY=sk-xxx \
  -v $(pwd)/configs:/app/configs:ro \
  -v /models/bge-reranker-large:/app/models/bge-reranker-large:ro \
  -v /tmp/logs/svc-model-reranker:/app/logs \
  aipaas/svc-model-reranker:latest

# 查看日志 / 就绪状态
docker logs -f svc-model-embedding
curl -s http://localhost:8003/ready
curl -s http://localhost:8004/ready
```

> 离线后端首次启动需加载本地模型（约十几秒到数分钟）；`/health` 只代表进程存活，`/ready` 才代表模型可服务。在线后端（`backend: online`）则无需挂载模型目录。

### 5.3 docker compose 中部署

#### 5.3.1 目录结构

```
deploy/docker/
├── .env.example               # 环境变量模板（拷贝为 .env 后修改）
├── docker-compose.yaml        # 主编排（include 基础编排；平台中间件 + Python 服务 + 前端）
└── docker-compose-base.yaml   # 基础编排（init-volumes + etcd / minio / milvus / attu / mongodb）
```

#### 5.3.2 启动顺序

依赖链：`init-app-dirs` → `milvus（来自 base 编排，依赖 etcd + minio）/ minio / mongodb` → `svc-model-embedding / svc-model-reranker` → `svc-ingest / svc-query` → `frontend`。**编排依赖 base 编排的 milvus / minio / mongodb**，主编排通过 `depends_on`（`service_healthy`）等待它们就绪；两个业务服务还会等待两个模型服务 `healthy` 后再启动。

推荐启动步骤：

```bash
cd deploy/docker

# 1. 拷贝并修改环境变量
cp .env.example .env
vim .env         # 修改 MINIO 密码、MONGO 密码、LLM 地址、模型目录 MODEL_BASE_DIR 等
                 # 同时确认 TORCH_VARIANT=gpu|cpu（决定模型服务镜像装哪版 torch，默认 gpu）

# 2. 启动模型服务（首次启动需加载本地模型，start_period 给到 120s）
docker compose up -d svc-model-embedding svc-model-reranker

# 3. 启动 Python AI 服务
#    depends_on 会自动拉起 base 编排的 milvus / minio / mongodb 及 init 容器，
#    并等待 milvus / minio / 两个模型服务 healthy 后再启动
docker compose up -d svc-ingest svc-query

# 4. 查看状态
docker compose ps

# 5. 查看实时日志
docker compose logs -f svc-ingest
docker compose logs -f --tail=100 svc-model-embedding

# 6. 停止
docker compose down
```

> 也可以先单独启动依赖的中间件确认 healthy，再启动服务：
>
> ```bash
> docker compose up -d milvus minio mongodb
> docker compose ps milvus minio mongodb
> docker compose up -d svc-model-embedding svc-model-reranker
> docker compose up -d svc-ingest svc-query
> ```

#### 5.3.3 compose 配置与寻址

| 配置项（`.env` / compose） | 值 | 说明 |
|---|---|---|
| `APP_ENV` | `devhome` / `devcompany`（默认 devcompany） | 选择配置覆盖层 |
| `CONFIG_DIR` | `/app/configs` | 容器内配置目录 |
| `CONFIG_PACKAGE_DIR` | `../../pymodules/configs` | 宿主机配置包，只读挂载到 `/app/configs` |
| `MODEL_BASE_DIR` | `../../models` | 宿主机模型根目录，分别挂载为 `bge-m3`、`bge-reranker-large` |
| `TORCH_VARIANT` | `gpu`（默认）/ `cpu` | 模型服务镜像的 torch 构建 |
| `INGEST_WORKER_PORT` / `QUERY_WORKER_PORT` | `8001` / `8002` | 业务服务监听端口（容器内 + 宿主机映射同源） |
| `MODEL_EMBEDDING_WORKER_PORT` / `MODEL_RERANKER_WORKER_PORT` | `8003` / `8004` | 模型服务监听端口（容器内 + 宿主机映射同源） |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MONGO_URL` / `OPENAI_API_KEY` / `MINERU_API_TOKEN` | — | 密钥，经 `env_file` + `environment` 注入 |

服务间寻址**不再通过环境变量**，而是由配置包中的地址决定：

| 配置节点 | base 默认值 | 说明 |
|---|---|---|
| `common.milvus.url` | `http://milvus:19530` | Milvus 地址（容器名） |
| `common.minio.endpoint` | `minio:9000` | MinIO 地址（host:port，不带 scheme） |
| `common.mongo.url` | `${oc.env:MONGO_URL}` | 连接串走环境变量 |
| `common.embedding_service.url` | `http://svc-model-embedding:8003` | 业务侧唯一入口，可用 `EMBEDDING_SERVICE_URL` 覆盖 |
| `common.rerank_service.url` | `http://svc-model-reranker:8004` | 业务侧唯一入口，可用 `RERANKER_SERVICE_URL` 覆盖 |

**端口统一规则**：四个 worker 的监听地址都由 `{模块名大写}_HOST` / `{模块名大写}_PORT` 环境变量覆盖（加载优先级：环境变量 > `{APP_ENV}.yaml` > `base.yaml`），默认值写在 `base.yaml` 里。改端口只需一处：

```bash
# 裸机 / 容器通用
INGEST_WORKER_PORT=9000 .venv/bin/python -m ingest_worker.api.service

# compose：改 deploy/docker/.env，容器监听、宿主机映射、服务间调用地址会一起变
INGEST_WORKER_PORT=9000 docker-compose up -d
```

`devhome` / `devcompany` 覆盖层会把这些地址改成对应的开发网络地址，部署时按实际环境选择 `APP_ENV`。compose 已为四个服务预填好挂载与依赖，见 [docker-compose.yaml](file:///coding/aipaas/deploy/docker/docker-compose.yaml)。

---

## 六、四个模块独立说明

四个模块共用同一套配置加载机制（`CONFIG_DIR` + `APP_ENV`，`extra="forbid"`），公共节点 `common` 见 [3.3 节](#33-配置加载与环境变量)；本章只列各模块自己的启动命令、接口与专属配置。

### 6.1 ingest-worker（端口 8001）

**职责**：接收文档上传 → LangGraph 编排（PDF转MD → 图片处理 → 文档切片 → 主体识别 → BGE 向量化 → 写入 Milvus）；入口 `ingest_worker.api.service:app`。

**快速启动**：

```bash
cd pymodules
uv run uvicorn ingest_worker.api.service:app --host 0.0.0.0 --port 8001 --reload
```

**接口清单**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针，返回 `{"status":"ok","service":"svc-ingest"}` |
| GET | `/import.html` | 文件导入前端页面 |
| POST | `/upload` | 多文件上传（form-data，字段 `files`），每个文件生成一个 `task_id` |
| GET | `/status/{task_id}` | 任务状态：`status` / `done_list` / `running_list` |

**测试**：

```bash
curl -s http://localhost:8001/health

# 上传文件（可重复 -F 传多个）
curl -s -X POST http://localhost:8001/upload -F "files=@./test.pdf"

# 轮询任务进度（task_id 来自上传响应）
curl -s http://localhost:8001/status/<task_id>
```

**配置项**（`ingest_worker` 命名空间）：

| 配置键 | 默认值 | 说明 |
|---|---|---|
| `max_content_length` | `500` | 切片最大长度 |
| `min_content_length` | `200` | 合并短内容的最小长度 |
| `item_name_chunk_k` | `3` | 主体（商品名）识别使用的切片数量 |
| `item_name_chunk_size` | `2500` | 主体识别使用的切片内容长度 |
| `image_extensions` | `.jpg/.jpeg/.png/.gif/.bmp/.webp` | 图片处理支持的扩展名 |

依赖的公共节点：`common.milvus`、`common.minio`、`common.mongo`、`common.mineru`、`common.llm`、`common.embedding_service`。

### 6.2 query-worker（端口 8002）

**职责**：接收用户查询 → LangGraph 编排（主体确认 → 向量检索 / HyDE 检索 / MCP Web 搜索 → RRF 融合 → 重排 → SSE 流式输出）；入口 `query_worker.api.service:app`。

**快速启动**：

```bash
cd pymodules
uv run uvicorn query_worker.api.service:app --host 0.0.0.0 --port 8002 --reload
```

**接口清单**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针，返回 `{"ok": true}` |
| GET | `/chat.html` | 查询前端页面 |
| POST | `/query` | 查询接口：`query` / `session_id` / `is_stream`；非流式直接返回 `answer`，流式返回 `session_id` 由前端订阅 `/stream/{session_id}` |
| GET | `/stream/{session_id}` | SSE 流式输出（`text/event-stream`） |
| GET | `/history/{session_id}` | 查询会话历史，`limit` 默认 50 |
| DELETE | `/history/{session_id}` | 清空会话历史 |

**测试**：

```bash
curl -s http://localhost:8002/health

# 非流式
curl -s -X POST http://localhost:8002/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"如何测量交流电压？","session_id":"demo-1","is_stream":false}'

# 流式：先拿 session_id，再订阅 SSE
curl -s -X POST http://localhost:8002/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"如何测量交流电压？","is_stream":true}'
curl -N http://localhost:8002/stream/<session_id>

# 历史
curl -s "http://localhost:8002/history/demo-1?limit=50"
curl -s -X DELETE http://localhost:8002/history/demo-1
```

**配置项**（`query_worker` 命名空间）：

| 配置键 | 默认值 | 说明 |
|---|---|---|
| `query_high_threshold` | `0.65` | 主体名确认高阈值 |
| `query_mid_threshold` | `0.55` | 主体名确认中阈值 |
| `rerank_max_topk` | `10` | 重排后保留条数硬上限（≤10） |
| `rerank_min_topk` | `3` | 最少保留条数 |
| `rerank_gap_abs` | `0.5` | 断崖截断绝对阈值 |
| `rerank_gap_ratio` | `0.25` | 断崖截断相对阈值 |
| `max_context_chars` | `12000` | 答案生成的上下文预算 |

依赖的公共节点：`common.milvus`、`common.mongo`、`common.llm`、`common.mcp`、`common.embedding_service`、`common.rerank_service`。

### 6.3 model-embedding-worker（端口 8003）

**职责**：嵌入模型独立推理服务——离线 BGE-M3（dense + sparse）或在线 DashScope / OpenAI 兼容接口（仅 dense）；入口 `model_embedding_worker.api.service:app`。

**快速启动**：

```bash
cd pymodules
uv run uvicorn model_embedding_worker.api.service:app --host 0.0.0.0 --port 8003 --reload
# 或沿用配置中的 host/port
uv run python -m model_embedding_worker.api.service
```

**接口清单**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针，不检查模型 |
| GET | `/ready` | 就绪探针，模型未加载返回 503 |
| GET | `/v1/model/info` | 当前后端、模型、设备、精度、向量维度 |
| POST | `/v1/embeddings` | 批量生成 dense + sparse 向量 |

**测试**：

```bash
curl -s http://localhost:8003/health
curl -s http://localhost:8003/ready
curl -s http://localhost:8003/v1/model/info

curl -s -X POST http://localhost:8003/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"embedding_documents":["如何测量交流电压？","烫金机"]}'
# 响应示例（离线后端；在线后端 sparse 为 []）
# {"dense":[[0.01,...],[0.02,...]],"sparse":[{"6":0.0237,"32":0.0498},{...}]}
```

> 稀疏向量的 key 经 JSON 序列化后是字符串，客户端 `aipaas_llm.lm.embedding_http_utils.generate_embeddings` 已自动还原为 int（Milvus 稀疏向量字段要求整数维度号）。

**配置项**（`model_embedding_worker` 命名空间）：

| 配置键 | 默认值 | 说明 |
|---|---|---|
| `backend` | `offline` | `offline`（本地 BGE-M3，dense + sparse）/ `online`（远端接口，仅 dense） |
| `host` / `port` | `0.0.0.0` / `8003` | 服务监听 |
| `max_batch_size` | `64` | 单请求最大文本条数，超限返回 413 |
| `max_concurrency` | `1` | 推理并发上限（CPU 建议 1，GPU 可调大） |
| `offline.model_path` | `/app/models/bge-m3` | 嵌入模型目录 |
| `offline.device` | `null` | `null` 自动探测（有 GPU 用 cuda，否则 cpu） |
| `offline.use_fp16` | `true` | 半精度 |
| `offline.normalize_embeddings` | `true` | 固定开启：Milvus IP 检索归一化后等价余弦 |
| `online.api_key` | 回退 `EMBEDDING_ONLINE_API_KEY` → `OPENAI_API_KEY` | 在线密钥 |
| `online.base_url` | DashScope 兼容地址 | 在线嵌入地址 |
| `online.model` / `online.dimension` | `text-embedding-v4` / `1536` | 在线模型与维度 |
| `online.timeout` | `60` | 在线请求超时（秒） |

### 6.4 model-reranker-worker（端口 8004）

**职责**：重排序模型独立推理服务——离线 bge-reranker-large 或在线 DashScope `TextReRank`；入口 `model_reranker_worker.api.service:app`。

**快速启动**：

```bash
cd pymodules
uv run uvicorn model_reranker_worker.api.service:app --host 0.0.0.0 --port 8004 --reload
# 或沿用配置中的 host/port
uv run python -m model_reranker_worker.api.service
```

**接口清单**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针 |
| GET | `/ready` | 就绪探针，模型未加载返回 503 |
| GET | `/v1/model/info` | 当前后端、模型、设备、归一化开关 |
| POST | `/v1/rerank` | 返回与 `documents` 顺序一一对应的分数 |

**测试**：

```bash
curl -s http://localhost:8004/health
curl -s http://localhost:8004/ready
curl -s http://localhost:8004/v1/model/info

curl -s -X POST http://localhost:8004/v1/rerank \
  -H 'Content-Type: application/json' \
  -d '{"query":"烫金机","documents":["烫金机","吹风机","烫金机的使用方法"]}'
# {"scores":[0.9999,0.1889,0.9874]}
```

打分固定 `normalize=True`，业务侧断崖截断阈值（0.5 / 0.25）依赖归一化分数。

**配置项**（`model_reranker_worker` 命名空间）：

| 配置键 | 默认值 | 说明 |
|---|---|---|
| `backend` | `offline` | `offline`（本地 bge-reranker-large）/ `online`（DashScope TextReRank） |
| `host` / `port` | `0.0.0.0` / `8004` | 服务监听 |
| `max_documents` | `128` | 单请求最大文档条数，超限返回 413 |
| `max_concurrency` | `1` | 推理并发上限 |
| `offline.model_path` | `/app/models/bge-reranker-large` | 重排序模型目录 |
| `offline.device` | `null` | `null` 自动探测 |
| `offline.use_fp16` | `true` | 半精度 |
| `offline.normalize` | `true` | 固定开启：业务断崖阈值按归一化分数设定 |
| `online.api_key` | 回退 `TEXT_RERANK_API_KEY` → `OPENAI_API_KEY` | 在线密钥 |
| `online.model` | `qwen3-rerank` | 在线重排序模型 |
| `online.instruct` | 内置说明 | 在线重排序指令 |

---

## 七、模型服务实现要点

### 7.1 目录结构

```text
apps/model-embedding-worker/            # 端口 8003
├─ pyproject.toml
├─ Dockerfile
└─ model_embedding_worker/
   ├─ model_service.py                  # 离线后端：BGE-M3 单例 + dense/sparse 编码
   ├─ online_service.py                 # 在线后端：OpenAI 兼容接口（仅 dense，sparse 预留）
   ├─ service.py                        # 后端门面：按 model_embedding_worker.backend 分派
   └─ api/service.py                    # FastAPI 接口（全异步）

apps/model-reranker-worker/             # 端口 8004
├─ pyproject.toml
├─ Dockerfile
└─ model_reranker_worker/
   ├─ model_service.py                  # 离线后端：FlagReranker 单例 + 归一化打分
   ├─ online_service.py                 # 在线后端：DashScope TextReRank
   ├─ service.py                        # 后端门面：按 model_reranker_worker.backend 分派
   └─ api/service.py                    # FastAPI 接口（全异步）
```

两个模块的配置 schema 在 `packages/common/aipaas_common/conf/schemas/`，默认值与覆盖在 `configs/aipaas_config.*.yaml`。

### 7.2 在线 / 离线双后端

| 服务 | 开关（配置项） | 取值 | 行为 |
|---|---|---|---|
| 嵌入 | `model_embedding_worker.backend` | `offline`（默认） | 本地 BGE-M3，输出 dense（1024）+ sparse（250002 词表） |
| 嵌入 |  | `online` | DashScope/OpenAI 兼容 embedding，只输出 dense，`sparse` 固定为空列表（预留扩展点） |
| 重排序 | `model_reranker_worker.backend` | `offline`（默认） | 本地 bge-reranker-large，`normalize=True` 打分 |
| 重排序 |  | `online` | DashScope `TextReRank` |

实现要点：
- 后端门面 `service.py` 用 `importlib` **延迟导入**，在线模式不会加载 `torch` / `pymilvus` / `FlagEmbedding`。
- 在线稀疏向量目前为空列表，后续在 `online_service.encode()` 内补齐即可，接口结构不变。
- 切换后端只需改 `aipaas_config.*.yaml` 中的 `backend` 并重启对应服务，业务侧无感知（调用地址不变）。

### 7.3 异步实现要点

两个 worker 的路由、后端门面、推理入口全部为 `async def`：

| 关注点 | 做法 |
|---|---|
| 模型单例加载 | `asyncio.Lock` 双检锁 + `asyncio.to_thread(_build_model)`，并发启动只加载一次 |
| CPU/GPU 密集推理 | `asyncio.Semaphore(max_concurrency)` 限流 + `asyncio.to_thread` 卸载到线程池，避免阻塞事件循环 |
| 在线嵌入 | `openai.AsyncOpenAI` 原生异步客户端，连接池复用 |
| 在线重排 | dashscope SDK 为同步实现，用 `asyncio.to_thread` 包装 |
| 请求体限制 | `max_batch_size` / `max_documents` 超限返回 413，空入参返回 400 |
| 错误返回 | 推理异常统一 500 + `{"detail": ...}`；日志用占位符传参（异常字符串常含大括号，f-string 会触发 loguru 格式化报错） |
| 启动预加载 | `lifespan` 中调用 `ensure_ready()`，失败不退出进程，`/ready` 持续 503 暴露原因 |

### 7.4 业务侧调用方式

调用方只改了 import，函数签名与返回结构不变：

| 文件 | import |
|---|---|
| `ingest-worker/.../f_node_bge_embedding.py` | `aipaas_llm.lm.embedding_http_utils.generate_embeddings` |
| `ingest-worker/.../e_node_item_name_recognition.py` | 同上 |
| `query-worker/.../a_node_item_name_confirm.py` | 同上 |
| `query-worker/.../b_node_search_embedding.py` | 同上 |
| `query-worker/.../c_node_search_embedding_hyde.py` | 同上 |
| `retrieval/aipaas_retrieval/milvus_utils.py` | 同上 |
| `query-worker/.../f_node_rerank.py` | `aipaas_llm.lm.reranker_http_utils.rerank_documents` |

调用地址与超时取自 `common.embedding_service` / `common.rerank_service`；`aipaas-llm` 不依赖 `dashscope` / `FlagEmbedding` / `pymilvus[model]`，只保留 `httpx` 客户端。

### 7.5 实测记录（Windows / CPU torch 2.11.0+cpu）

| 验证项 | 结果 |
|---|---|
| `GET /health` | `{"status":"ok","service":"model-embedding-worker"}` |
| `GET /ready`（嵌入，离线） | 启动约 12s 可服务，`dim={"dense":1024,"sparse":250002}` |
| `POST /v1/embeddings`（离线） | 2 条文本 → dense 2×1024（L2 范数 = 1.0）+ sparse 正常 |
| `POST /v1/rerank`（离线） | `烫金机/吹风机/烫金机的使用方法` → `0.99993 / 0.18893 / 0.98740` |
| 4 并发嵌入请求 | 全部返回 2×1024，串行闸门生效（`max_concurrency=1`） |
| 在线嵌入 `/ready` | `backend=online, model=text-embedding-v4, dim=1536, sparse_supported=false` |
| 在线重排 `/ready` | `backend=online, model=qwen3-rerank` |
| 在线接口错误处理 | 无效密钥时返回结构化 `{"detail": ...}`（HTTP 500），进程不崩 |
| `uv lock` | 183 包解析通过，10 个 workspace 成员全部锁定 |

### 7.6 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| `/ready` 一直 503 | 模型加载失败 | 看服务日志；确认 `offline.model_path` 指向真实目录且已挂载进容器 |
| `torch.cuda.is_available()` 为 False | 装了 CPU 版 torch | 按 4.4 节替换为 cu128 构建，并把 `offline.device` 设为 `cuda` |
| 业务报 `All connection attempts failed` | 服务未启动或地址不对 | 检查 `common.embedding_service.url` / `common.rerank_service.url`，先请求 `/health` |
| 413 | 超过 `max_batch_size` / `max_documents` | 调用方分批，或调大配置 |
| 在线后端 401 `invalid_api_key` | 密钥无效或过期 | 配置 `OPENAI_API_KEY` / `EMBEDDING_ONLINE_API_KEY` / `TEXT_RERANK_API_KEY`；或把 `backend` 切回 `offline` |
| 业务侧拿到空 `sparse` | 服务端 `backend: online` | 在线后端不产稀疏向量，Milvus 混合检索需切回 `offline` |
| 日志出现 `KeyError: "'error'"` | 把含大括号的异常字符串直接塞进 f-string 记日志 | 改用 `logger.error("...：{}", exc, exc_info=True)` |
| `uv lock` 报镜像版本不全 | 多个 PyPI 镜像版本滞后 | 根 `pyproject.toml` 已设 `index-strategy = "unsafe-best-match"` |

---

## 八、日志与健康检查

### 8.1 实现位置

[packages/common/aipaas_common/core/log.py](file:///coding/aipaas/pymodules/packages/common/aipaas_common/core/log.py)，基于 **loguru** 实现，在 `import` 期提供控制台兜底，配置加载完成后再按 `common.logging` 重建 sink。

### 8.2 配置项

日志由配置包的 `common.logging` 节点驱动（非环境变量）：

| 配置键 | 默认值 | 说明 |
|--------|--------|------|
| `file_prefix` | `null` | 日志文件名前缀；为 `null` 时按模块名推导（下划线转连字符，如 `ingest_worker` → `ingest-worker`）。 |
| `console_enable` | `true` | 是否启用控制台输出。 |
| `console_level` | `INFO` | 控制台日志级别。 |
| `file_enable` | `true` | 是否启用文件输出。 |
| `file_level` | `INFO` | 文件日志级别。 |
| `file_retention` | `7 days` | 日志保留策略，支持 loguru 的 retention 语法（如 `"14 days"`、`"30 days"`）。 |

> 日志目录固定为 `PROJECT_ROOT/logs`，`PROJECT_ROOT` 优先取同名环境变量，容器内必须为 `/app`。

### 8.3 日志文件命名规则

```
{file_prefix 或 module_name 推导值}_YYYYMMDD.log
```

示例：
- 开发机 ingest-worker：`pymodules/logs/ingest-worker_20261110.log`
- 容器内 ingest-worker：`/app/logs/ingest-worker_20261110.log`
- 容器内 query-worker：`/app/logs/query-worker_20261110.log`

### 8.4 日志轮转与清理

- **轮转**：loguru 配置 `rotation="00:00"`，每天 0 点自动创建新文件。
- **清理**：loguru 根据 `file_retention` 自动删除过期文件（默认 7 天）。
- **编码**：UTF-8，彻底解决中文乱码。
- **异步入队**：`enqueue=True`，支持多线程 / asyncio 场景不丢失日志。

### 8.5 容器内日志挂载

四个 Dockerfile 都声明了 `VOLUME ["/app/logs", "/app/models"]`。compose 进一步把 `/app/logs` 映射到宿主机目录：

```yaml
# docker-compose.yaml 中 svc-ingest 的 volumes
volumes:
  - ${DOCKER_LOG_DIRECTORY:-.}/logs/svc-ingest:/app/logs
```

宿主机日志目录由 `.env` 中的 `DOCKER_LOG_DIRECTORY` 控制，默认相对路径为 `deploy/docker/logs/<服务名>/`。主编排的 `init-app-dirs` 会在启动前预创建这些目录并修正属主。

### 8.6 如何使用

在任何 workspace 成员里直接 import：

```python
from aipaas_common.core.log import logger

logger.info("导入任务开始", task_id=task_id)
logger.warning(f"文件上传 MinIO 失败，本地继续处理: {e}")
logger.exception("LangGraph 全流程执行失败")  # 自动带完整堆栈
```

日志还内置了 **request_id** 上下文支持（`inject_request_id` patch），通过 `aipaas_common.core.context.request_id_ctx_var.set(...)` 注入后，每条日志都会自动带上对应的 request_id。

### 8.7 健康检查

| 服务 | 端点 | Dockerfile healthcheck | compose healthcheck |
|------|------|------------------------|---------------------|
| svc-ingest | `/health` ✅ | ✅ 已声明 | ✅ 已声明 |
| svc-query | `/health` ✅ | ✅ 已声明 | ✅ 已声明 |
| svc-model-embedding | `/health`（存活）+ `/ready`（就绪，模型未加载返回 503） | ✅ 已声明 | ✅ 已声明 |
| svc-model-reranker | `/health`（存活）+ `/ready`（就绪，模型未加载返回 503） | ✅ 已声明 | ✅ 已声明 |

> 容器探针统一探测 `/health`；两个模型服务的 `/health` 只反映进程存活，`/ready` 才反映模型是否可服务，业务侧与运维排查用它。启动预加载失败不会让进程退出，而是 `/ready` 持续 503 暴露原因。

---

## 九、扩展规范（新增共享包 / 新增应用包）

### 9.1 在 `packages/` 新增共享包

假设新增包名为 `packages/vector-store/`，Python 包名为 `aipaas_vector_store`：

**步骤 1**：建目录

```bash
mkdir -p packages/vector-store/aipaas_vector_store
touch packages/vector-store/aipaas_vector_store/__init__.py
```

**步骤 2**：写 `pyproject.toml`（参考 `packages/pipeline/pyproject.toml`，最简单只依赖 `aipaas-common`）

```toml
[project]
name = "aipaas-vector-store"
version = "0.1.0"
description = "向量存储层"
requires-python = ">=3.12"
dependencies = [
    "aipaas-common",
]

[tool.uv.sources]
aipaas-common = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["aipaas_vector_store"]
```

**步骤 3**：根 `pyproject.toml` 追加 members

```toml
[tool.uv.workspace]
members = [
    ...
    "packages/vector-store",   # 新增
]
```

**步骤 4**：被依赖的包追加 `[tool.uv.sources]`

如果 `svc-ingest` 要依赖新包，执行：

```bash
uv add --package svc-ingest aipaas-vector-store
```

uv 会自动在 `apps/ingest-worker/pyproject.toml` 的 `[tool.uv.sources]` 中追加 `aipaas-vector-store = { workspace = true }`。

**步骤 5**：重新锁文件并同步

```bash
uv lock && uv sync --all-packages --group cpu
```

**步骤 6**：校验

```bash
# 冒烟
uv run python -c "import aipaas_vector_store; print('ok')"
# 依赖方向校验（packages/ 下不得 import apps/）
grep -rn "from ingest_worker\|from query_worker\|import ingest_worker\|import query_worker" packages/
# 应该返回 0 行
```

### 9.2 在 `apps/` 新增应用包

假设新增 `apps/search-worker/`：

**步骤 1-5**：同新增包流程（目录 → pyproject.toml → 根 workspace members → uv lock & sync）。

**步骤 6**：编写 Dockerfile（参考 ingest-worker / query-worker 的双阶段模板）

**步骤 7**：`deploy/docker/docker-compose.yaml` 追加服务定义（build context、environment、ports、volumes、healthcheck、depends_on）。关键要素：
- 环境变量中必须设置 `APP_ENV`、`CONFIG_DIR=/app/configs` 及所需密钥
- 挂载配置包 `${CONFIG_PACKAGE_DIR:-../../pymodules/configs}:/app/configs:ro`
- 日志卷挂载 `${DOCKER_LOG_DIRECTORY}/logs/<服务名>:/app/logs`
- healthcheck 指向 `/health`
- depends_on 声明依赖的 base 编排中间件（milvus / minio / mongodb，`service_healthy`）与主编排的 `init-app-dirs`（`service_completed_successfully`）

**步骤 8**：在 `configs` 中新增该模块的命名空间与 schema，并在 FastAPI app 中实现 `/health` 端点。

---

## 十、测试与验证清单

### 10.1 编译检查

```bash
cd pymodules
uv run python -m compileall -q packages/ apps/ingest-worker/ apps/query-worker/ apps/model-embedding-worker/ apps/model-reranker-worker/
# 无输出 = 全部通过
```

### 10.2 import 冒烟测试

```bash
# 只测 workspace 内的正式成员
uv run python -c "
import aipaas_common
import aipaas_llm
import aipaas_retrieval
import aipaas_storage
import aipaas_pipeline
import aipaas_mcp_agents
import ingest_worker
import query_worker
import model_embedding_worker
import model_reranker_worker
print('all imports ok')
"
```

### 10.3 依赖方向校验（禁止 packages → apps 反向）

```bash
cd pymodules
# 正向：packages 内的包不得 import apps 目录
grep -rn "from ingest_worker\|from query_worker\|import ingest_worker\|import query_worker" packages/
# 理想输出：0 行

# 正向：下层包不得 import 上层包
grep -rn "from aipaas_llm\|from aipaas_retrieval" packages/common/
grep -rn "from aipaas_retrieval" packages/llm/
# 理想输出：0 行
```

### 10.4 端点验证

```bash
curl -s http://localhost:8001/health   # ingest-worker → {"status": "ok", "service": "svc-ingest"}
curl -s http://localhost:8002/health   # query-worker → {"ok": true}
curl -s http://localhost:8003/ready    # 嵌入模型 → 200 且含 backend / dim
curl -s http://localhost:8004/ready    # 重排序模型 → 200 且含 backend / model
```

### 10.5 日志文件验证

```bash
cd pymodules
export CONFIG_DIR=$(pwd)/configs APP_ENV=devhome
uv run uvicorn ingest_worker.api.service:app --port 8001 &

# 查看日志文件是否按预期命名（前缀由模块名推导）
ls -la logs/ingest-worker_*.log
tail -f logs/ingest-worker_*.log
```

### 10.6 Docker 镜像构建验证

```bash
cd pymodules
docker build -f apps/ingest-worker/Dockerfile -t test-ingest . 2>&1 | tail -20
# 最终应看到 "Successfully tagged test-ingest:latest"
```

---

## 十一、常见问题

### Q1：`ModuleNotFoundError: No module named 'aipaas_common'`

**原因**：忘记加 `--all-packages`，裸 `uv sync` 只装根项目（空依赖）。

**修复**：
```bash
cd pymodules
uv sync --all-packages
uv run python -c "import aipaas_common; print('ok')"
```

### Q2：启动即报配置错误（`配置路径不存在` / `APP_ENV 非法` / `配置缺少模块命名空间`）

**原因**：`CONFIG_DIR` 指向的目录或 YAML 文件不存在；`APP_ENV` 不是 `devhome` / `devcompany`；YAML 中缺少该模块命名空间；或字段名拼错（Pydantic `extra="forbid"`）。

**修复**：确认 `configs/` 下三个文件齐全，显式设置：
```bash
export CONFIG_DIR=$(pwd)/configs APP_ENV=devhome
```
启动日志会打印 `module / env / dir / common_keys / module_keys`，可据此核对加载结果。

### Q3：compose 内连不上 milvus / minio，报 `Connection refused`

**原因**：把 `aipaas_config.*.yaml` 里的地址改成了 `localhost` / `127.0.0.1`（容器内指向自身）；或所选 `APP_ENV` 覆盖层指向了容器不可达的宿主机地址。

**修复**：base 层已用容器名（`http://milvus:19530`、`minio:9000`、`http://svc-model-embedding:8003`）。容器部署时确认配置包正确挂载到 `/app/configs`，并按实际网络选择 `APP_ENV`。

### Q4：容器启动后日志目录不存在或为空

**原因**：`PROJECT_ROOT` 未设置导致日志目录推导到非预期路径；或 `/app/logs` 卷未正确挂载。

**修复**：镜像内已设置 `PROJECT_ROOT=/app`，compose 也已挂载日志卷。若手动 `docker run`，务必加：
```bash
-e PROJECT_ROOT=/app \
-v /host/logs/svc-ingest:/app/logs
```

### Q5：模型下载慢，每次重建镜像都要重新下

**原因**：Dockerfile 声明了 `MODELSCOPE_CACHE=/app/models/modelscope_cache` 和 `HF_HOME=/app/models/huggingface`，但没挂载卷，每次新容器都是空的。

**修复**：挂载模型缓存卷到宿主机持久化：
```bash
docker run ... \
  -v /host/models/svc-ingest:/app/models \
  aipaas/svc-ingest:latest
```

compose 中也可追加：
```yaml
svc-ingest:
  volumes:
    - ${DOCKER_LOG_DIRECTORY}/logs/svc-ingest:/app/logs
    - ${DOCKER_MODEL_DIRECTORY:-./models}/svc-ingest:/app/models
```

### Q6：`uv lock --check` 失败

**原因**：有人改了 `pyproject.toml` 里的依赖但忘了重新 `uv lock`。

**修复**：
```bash
uv lock && uv sync --all-packages --group cpu
```

### Q7：GPU 机部署时 PyTorch 报 CUDA not available

**原因**：同步时选了 `--group cpu`（或未选分组），装的是 CPU 版 torch；镜像构建时 `TORCH_VARIANT=cpu`；或配置中 `offline.device` 仍为 `cpu`。

**修复**：重新按 GPU 分组同步（两组互斥，直接切换即可）：

```bash
cd pymodules
uv sync --all-packages --group gpu
```

镜像侧则用 `--build-arg TORCH_VARIANT=gpu` 重构建，或在 `deploy/docker/.env` 里设 `TORCH_VARIANT=gpu` 后 `docker compose build svc-model-embedding svc-model-reranker`；同时把配置里的 `offline.device` 设为 `cuda`。

### Q8：模型服务 `docker build` 在 `uv sync` 阶段失败（torch 下载报错）

典型报错：

```
error: Failed to download `torch==2.11.0+cpu`
  cause: error decoding response body
  cause: network unreachable
# 或 gpu 分组：
ReadTimeoutError: HTTPSConnectionPool(host='pypi.nvidia.cn', port=443): Read timed out.
```

**原因**：torch wheel（CPU 181MB / CUDA 版数 GB）托管在 `download.pytorch.org`，其中 NVIDIA 子依赖会经 DNS 跳到 `pypi.nvidia.cn`。docker 默认 bridge 网络经常连不上这些 CDN，表现为 `network unreachable` 或读超时；该问题与 uv / pip 无关，换成 pip 也一样失败。

**修复**：构建时让构建容器走宿主机网络：

```bash
docker build --network host ...          # 单镜像构建
```

compose 已在两个模型服务的 `build.network: host` 中配好，直接 `docker compose build` 即可。

**附带问题**：日志里 `Sending build context to Docker daemon 1.868GB` 是因为缺少 `.dockerignore`，把 `pymodules/.venv`（≈1.9GB）打进了构建上下文。仓库已提供 [.dockerignore](file:///coding/aipaas/pymodules/.dockerignore) 排除 `.venv` / `logs` / `tests` / `__pycache__`，上下文约 4MB。

---

## 附录：快速参考卡

```bash
# ---------- 开发环境 ----------
cd pymodules && uv sync --all-packages --group cpu   # 一次性装好全部（GPU 机换 --group gpu）
uv lock --check                             # 校验锁文件
export CONFIG_DIR=$(pwd)/configs APP_ENV=devhome   # 配置包 + 环境覆盖层

# ---------- 开发启动（先模型，后业务） ----------
uv run uvicorn model_embedding_worker.api.service:app --port 8003 --reload
uv run uvicorn model_reranker_worker.api.service:app  --port 8004 --reload
uv run uvicorn ingest_worker.api.service:app --port 8001 --reload
uv run uvicorn query_worker.api.service:app  --port 8002 --reload

# ---------- 依赖变更 ----------
uv add    --package aipaas-retrieval elasticsearch>=8.0.0
uv remove --package aipaas-llm dashscope
uv lock && uv sync --all-packages --group cpu

# ---------- 镜像构建 ----------
cd pymodules
docker build -f apps/ingest-worker/Dockerfile -t aipaas/svc-ingest .
docker build -f apps/query-worker/Dockerfile  -t aipaas/svc-query  .
# 模型服务：--network host 必加；TORCH_VARIANT 决定 CPU / GPU 版（默认 gpu）
docker build --network host --build-arg TORCH_VARIANT=gpu -f apps/model-embedding-worker/Dockerfile -t aipaas/svc-model-embedding .
docker build --network host --build-arg TORCH_VARIANT=gpu -f apps/model-reranker-worker/Dockerfile  -t aipaas/svc-model-reranker  .

# ---------- compose ----------
cd deploy/docker
docker compose up -d svc-model-embedding svc-model-reranker   # 先起模型服务
docker compose up -d svc-ingest svc-query   # 自动拉起依赖的 base 中间件（milvus/minio/mongodb）
docker compose logs -f svc-ingest
```
