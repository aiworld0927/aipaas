# 平台侧接口（apps/apis）

> 返回总文档：[README.md](../README.md)

## 1. 职责与定位

`apps/apis` 是 AIPaaS 平台侧的聚合入口，围绕「模型镜像构建、发布、部署、开发容器分配、任务与端口管理」提供一组 HTTP 接口。它属于历史平台服务代码，与知识库导入/查询链路相互独立。

- 入口：`apps/apis/aipaas_main.py`
- distribution 名：无（未纳入 uv workspace，无 `pyproject.toml`）
- 默认端口：`8000`（写死在 `__main__` 中）
- 独立部署：否（当前未纳入 monorepo 构建体系）

## 2. 目录结构

```
apps/apis/
├── aipaas_main.py        # FastAPI 入口：动态加载 routers
├── aipaas_lifespan.py    # 生命周期（当前仅有日志）
├── lifespan.py           # 早期版本生命周期，保留
├── pymod.py              # 模块动态加载的实验脚本
├── routers/
│   ├── deploysvc.py      # 镜像构建/发布/部署/开发容器
│   ├── item.py           # 商品管理示例路由
│   └── user.py           # 用户管理示例路由
└── svc/
    ├── deploysvc.py      # 部署服务实现
    ├── svc_image.py      # 镜像相关
    ├── svc_container.py  # 开发/计算/自动化学习容器
    ├── svc_pipeline.py   # 流水线
    ├── svc_port.py       # 容器在线端口分配
    ├── svc_task.py       # 任务管理
    ├── svc_reflux.py     # 数据回流
    ├── jobs_schedule.py  # 定时任务（Redis）
    ├── sshutil.py        # SSH/远程执行工具
    ├── util_db.py        # MySQL 持久化
    ├── util_k8s.py       # Kubernetes 操作
    ├── util_common.py    # 通用响应工具
    ├── _bak/             # 历史备份
    └── util/             # 文件服务、Redis、K8s 等工具
```

## 3. 入口与路由加载

### 3.1 应用创建

`aipaas_main.py`：

```python
app = FastAPI(title="AIPaaS访问入口", lifespan=lifespan)

def _dynamic_load_routes(app):
    router_path = Path(__file__).parent / "routers"
    model_list = [route.stem for route in router_path.glob("*.py")
                  if not route.name.startswith("__")]
    for mod_name in model_list:
        _mod = importlib.import_module(f"app.apis.routers.{mod_name}")
        if hasattr(_mod, "router"):
            app.include_router(_mod.router)
```

- 扫描 `routers/*.py`，按文件名动态导入并注册其中的 `router`。
- `app.state.conf` 当前为占位值 `{"hello": "world"}`。
- 根路由 `GET /` 返回欢迎语。
- `__main__` 中以 `uvicorn.run(app="aipaas_main:app", host="0.0.0.0", port=8000, reload=True)` 启动。

### 3.2 当前包路径问题

入口仍使用旧工程布局：

```python
from app.core.log import logger
from app.apis.aipaas_lifespan import lifespan
```

动态导入也固定为 `app.apis.routers.{mod_name}`。在新 monorepo 中，`apps/apis` 并不是名为 `app` 的包，因此**直接运行会因 `app.*` 导入失败**。历史上需要把 `apps/` 的父目录加入 `sys.path` 且将目录命名为 `app`，或补齐同名包结构。

### 3.3 生命周期

`aipaas_lifespan.py` 只输出启动/关闭日志，数据库、Kubernetes、文件服务器、Embedding、Qdrant、ES 等初始化代码均已注释，未实际接入。`lifespan.py` 是更早的副本，保留但未被入口使用。

## 4. HTTP 接口

### 4.1 deploysvc 路由

`routers/deploysvc.py` 使用统一响应包裹：

```json
{"ROOT": {"HEADER": {}, "RETURN_CODE": 0, "RETURN_MSG": "success", "BODY": {}}}
```

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/ImageList` | 查询镜像列表 |
| POST | `/PackageSearch` | 检索 PyPI 包 |
| POST | `/PackageSearchNew` | 新版包检索 |
| POST | `/ImageConstruct` | 构建并推送模型镜像 |
| POST | `/ImageConstruct_pypi` | 基于 PyPI 源构建镜像 |
| POST | `/TargetSelect` | 选择部署目标 |

内部还实现了容器列表持久化、部署状态文件、后台部署线程、镜像构建与推送、pip 版本匹配等辅助逻辑；部署过程通过 `qry_id` 与状态文件跟踪进度。

### 4.2 示例路由

| 前缀 | 路径 | 说明 |
|---|---|---|
| `/items` | `GET /items/`、`GET /items/{item_id}` | 商品列表/详情示例，返回固定文案 |
| `/users` | `GET /users/`、`GET /users/{user_id}` | 用户列表/详情示例，返回固定文案 |

### 4.3 svc 模块职责

| 模块 | 职责 |
|---|---|
| `svc/deploysvc.py` | 模型镜像构建、发布、部署、开发容器分配的核心实现 |
| `svc/svc_image.py` | 镜像查询、拉取、推送 |
| `svc/svc_container.py` | 开发容器、计算容器、自动化学习容器的创建与管理 |
| `svc/svc_pipeline.py` | 训练/推理流水线 |
| `svc/svc_port.py` | 为容器分配在线端口（K8s/MySQL） |
| `svc/svc_task.py` | 任务队列与任务状态管理 |
| `svc/svc_reflux.py` | 数据回流 |
| `svc/jobs_schedule.py` | 基于 Redis 的定时任务 |
| `svc/sshutil.py` | Paramiko SSH、远程执行、端口探测 |
| `svc/util_db.py` | MySQL 连接池与持久化 |
| `svc/util_k8s.py` | Deployment 等 Kubernetes 资源操作 |
| `svc/util_common.py` | Flask 风格统一响应 |

## 5. 运行与部署现状

- **未纳入 workspace**：根 `pyproject.toml` 的 members 不包含 `apps/apis`，`uv sync --all-packages` 不会安装它。
- **无 Dockerfile、无 pyproject**：不在当前镜像构建体系内，也不参与 Nacos 注册。
- **技术栈混杂**：FastAPI 路由之下，`svc/*` 仍大量使用 Flask 的 `request` / `Response`、`loguru`、`paramiko`、`kubernetes`、MySQL、Redis 等，运行需要额外依赖与外部环境。
- **总文档定位**：平台 API 暂不处理、未纳入 workspace；知识库两条主链路（导入、查询）不依赖它。

历史上以如下方式启动（需自行补齐旧包结构与依赖，当前仓库状态下不可直接运行）：

```bash
python apps/apis/aipaas_main.py   # 端口 8000，reload=True
```

## 6. 依赖关系

```
apps/apis（独立于 workspace）
├── FastAPI / Uvicorn
├── Flask（svc 层）
├── loguru
├── paramiko（SSH）
├── kubernetes
├── pymysql / MySQL 连接池
├── redis
└── requests / PyYAML 等
```

它**不依赖** `aipaas-common`、`aipaas-model-clients`、`aipaas-retrieval`、`aipaas-storage`、`aipaas-nacos`，也不被其他模块依赖。

## 7. 注意事项

- `routers/deploysvc.py` 会执行 `docker build/push`、SSH 远程命令、写状态文件等有副作用的操作，误调用可能影响真实环境。
- `svc/` 下文件体积大、历史分支多，且存在 `_bak` 备份，阅读时优先从 `routers` 入口定位调用链。
- `lifespan.py` 与 `aipaas_lifespan.py` 内容重叠，修改生命周期逻辑前先确认入口实际引用的是哪一个。
- 如需把平台接口迁入新体系，建议按 `apps/app-query-worker` 的模式重建包结构（配置引导、依赖注入、独立 pyproject/Dockerfile），而不是直接在现有文件上打补丁。
