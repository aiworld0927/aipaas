# aipaas

人工智能平台即服务（AI PaaS）：面向金融标准化的知识库问答平台，由 Python AI 服务、Java 微服务平台、React 前端与部署编排组成。

## 软件架构

| 模块 | 技术栈 | 说明 |
| --- | --- | --- |
| [pymodules](pymodules/README.md) | Python 3.12 / uv workspace / FastAPI / LangGraph | 文档导入（:8000）与知识库查询（:8001）AI 服务 |
| [web](web/README.md) | React 18 / Vite / nginx | 对话、文件导入、个人中心前端 |
| [javaplat](javaplat) | Java 服务构建目录 | gateway / svc-document / svc-search（当前仅含 Dockerfile） |
| [deploy](deploy) | Docker Compose / K8s | 中间件（Milvus、etcd、MinIO、MongoDB）、Nacos、离线包与初始化脚本 |
| [docs](docs) | Markdown | 设计文档与待办事项 |
| [eval](eval) | Python | 检索与问答效果评测脚本 |

Python 侧 `pymodules` 采用 **uv workspace monorepo** 结构：`apps/` 存放可独立部署的业务服务，`libs/` 存放共享库，依赖单向流动（apps → libs）。详见 [pymodules/README.md](pymodules/README.md)。

## 安装教程

### 1. 启动中间件与依赖服务

```bash
cd deploy
source scripts/setup-env.sh      # 导出中间件地址、JAVA_HOME、DOCKER_VOLUME_DIRECTORY 等
docker-compose -f docker-compose.yaml up -d
```

`docker-compose.yaml` 内置 `init-volumes` 一次性容器，会自动创建数据目录并把属主对齐到当前执行用户，无需手工 `chown`。

### 2. 启动 Python 服务

```bash
cd pymodules
uv sync
uv run python apps/query/api/service.py      # 查询服务 http://127.0.0.1:8001
uv run python apps/ingest/api/service.py     # 导入服务 http://127.0.0.1:8000
```

### 3. 启动前端

```bash
cd web
npm install && npm run dev                   # 开发模式 http://localhost:5173
```

生产构建与容器运行方式见 [web/README.md](web/README.md#docker-构建与运行)。

## 使用说明

1. 前端登录后进入 `/chat` 进行知识库问答，支持流式（SSE）与非流式返回
2. `/import` 上传文档，自动触发解析、切分、向量化与入库流程
3. 健康检查：查询服务 `GET /health`；中间件端口映射见 [deploy/docker-compose.yaml](deploy/docker-compose.yaml)

## 参与贡献

1. Fork 本仓库
2. 新建 `Feat_xxx` 分支
3. 提交代码
4. 新建 Pull Request
