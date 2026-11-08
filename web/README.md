# AIPAAS 前端（React + Vite）

金融标准化知识库前端工程，仿 DeepSeek 对话风格重构。

## 快速开始

```bash
cd web
npm install
npm run dev      # 开发：http://localhost:5173
npm run build    # 构建：产物在 dist/
npm run preview  # 本地预览构建产物
```

## Docker 构建与运行

### 构建与启动

```bash
# 构建（在仓库根目录执行）
cd web && docker build -t aipaas-frontend:v1.0 .

# 运行（本机开发机快速验证，upstream 指向后端服务）
docker run -d --name aipaas-frontend \
    -p 80:80 \
    -e QUERY_API_UPSTREAM="http://aipaas.aiworld.com:8001" \
    -e IMPORT_API_UPSTREAM="http://aipaas.aiworld.com:8000" \
    aipaas-frontend:v1.0
```

- `QUERY_API_UPSTREAM` / `IMPORT_API_UPSTREAM` 是容器启动时由 `envsubst` 注入 nginx 的反代目标，**必须用双引号包裹**；反引号会被 shell 优先执行，直接报命令找不到。
- 访问 `http://<宿主机IP>/`，探活接口 `http://<宿主机IP>/health` 应返回 `ok`。

### 关键设计

- **多阶段构建**：build 阶段 `node:20-alpine` 执行 `npm ci` + `vite build`，runtime 阶段 `nginx:alpine` 只拷贝 `dist/` → 最终镜像约 20MB（build 阶段约 500MB）
- **缓存友好**：先 COPY `package.json + package-lock.json` 执行 `npm ci`，再 COPY 源码 → 修改 `src/**` 不会触发依赖重装
- **反代与 dev 对齐**：nginx 的 `/query-api`、`/import-api` 与 `vite.config.js` 的 dev proxy 完全一致，切换到生产无需改前端代码
- **运行时注入后端地址**：`nginx.conf.template` 用 `${QUERY_API_UPSTREAM}` / `${IMPORT_API_UPSTREAM}` 占位，容器启动时 `envsubst` 替换 → 构建期不需要知道后端地址，运行期通过 `-e` 或 docker-compose 传入
- **请求时解析 DNS**：upstream 采用「变量 + resolver」写法，域名在请求时解析，DNS 暂时不可用也不会导致容器启动失败

### 与后端的对应关系

| 前端路径 | 开发代理（Vite） | 生产反代变量 | 说明 |
| --- | --- | --- | --- |
| `/query-api/` | `http://127.0.0.1:8001` | `QUERY_API_UPSTREAM` | 查询服务 |
| `/import-api/` | `http://127.0.0.1:8000` | `IMPORT_API_UPSTREAM` | 导入服务（pymodules 的 ingest） |

### docker-compose 中配置

```yaml
frontend:
  image: aipaas-frontend:v1.0
  environment:
    QUERY_API_UPSTREAM: http://python-query:8001
    IMPORT_API_UPSTREAM: http://python-ingest:8000
  ports: ["80:80"]
```

注意：两个后端服务名和端口需按实际部署调整。本项目 pymodules 中查询服务监听 `8001`、导入服务监听 `8000`，请确认 `QUERY_API_UPSTREAM` / `IMPORT_API_UPSTREAM` 分别指向正确服务。

## 工程目录结构

```
web/                              # 前端工程根目录
├── index.html                  # SPA 入口
├── package.json
├── vite.config.js              # Vite 配置（含 /query-api、/import-api 开发代理）
├── Dockerfile                  # 多阶段构建（node 构建 → nginx 运行）
├── nginx.conf.template         # nginx 模板，运行时 envsubst 注入反代目标
├── .env.example                # 环境变量示例（API 地址）
└── src/
    ├── main.jsx                # React 挂载入口
    ├── App.jsx                 # 路由定义（登录页独立，业务页统一布局）
    ├── styles/
    │   └── global.css          # 全局样式 / 通用按钮 / 输入框
    ├── api/                    # API 层（与后端接口一一对应）
    │   ├── config.js           # API 地址环境配置
    │   ├── chat.js             # 查询服务（8001）：/query /stream /history /health
    │   └── importer.js         # 导入服务（8000）：/upload /status
    ├── utils/
    │   ├── auth.js             # 登录态管理（当前 Mock，后端就绪后替换）
    │   ├── sessions.js         # 会话列表管理（当前 localStorage，后端就绪后替换）
    │   └── answerParser.js     # 答案/图片解析（移植自旧版 chat.html）
    ├── components/
    │   ├── AppLayout.jsx       # 统一布局：顶部导航 + 左侧对话历史侧边栏
    │   ├── RequireAuth.jsx     # 登录路由守卫
    │   ├── ProgressPanel.jsx   # 阶段进度面板（时间轴 + 进度条 + 状态徽标）
    │   └── AnswerBody.jsx      # 答案正文（文本 + 参考图片）
    └── pages/
        ├── Login.jsx           # 登录页（成功后跳转 /chat）
        ├── Chat.jsx            # 对话页（流式 SSE / 非流式 / 历史）
        ├── Import.jsx          # 文件导入页（拖拽上传 + 任务轮询）
        └── UserCenter.jsx      # 个人中心
```

## 路由

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/login` | 登录页 | 未登录访问业务页会被重定向至此 |
| `/chat` | 对话页 | 新会话模式（首条消息自动建会话） |
| `/chat/:sessionId` | 对话页 | 打开指定历史会话 |
| `/import` | 文件导入页 | 侧边栏底部入口进入，页内可返回对话 |
| `/user` | 个人中心 | 顶栏用户头像入口 |

## 与后端的对接

- 开发环境通过 Vite 代理转发，避免跨域：
  - `/query-api/**` → `http://127.0.0.1:8001/**`
  - `/import-api/**` → `http://127.0.0.1:8000/**`
- 生产环境通过 `.env` 配置 `VITE_QUERY_API_BASE` / `VITE_IMPORT_API_BASE`。

## 已知限制（详见 docs/05_待办事项.md）

- 登录/个人中心为本地 Mock（后端缺少 `/api/auth/*`、`/api/user/*` 接口）。
- 对话历史列表暂存 localStorage（后端缺少 `/api/sessions` 系列接口）。
- 旧版页面 `pymodules/app/rag/pages/*.html` 仍保留，待联调稳定后下线。
