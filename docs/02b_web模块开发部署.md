# 前端（web/）模块开发部署指南

> 本文档描述 AIPaaS 前端模块的开发环境搭建、生产构建、Docker 镜像构建与运行、docker compose 部署，以及常见问题排查。

---

## 1. 模块简介

AIPaaS 前端是一个 Vite + React 的单页应用（SPA），为 RAG 问答、文档导入、用户登录等功能提供 Web 界面。

- **项目位置**：`/coding/aipaas/web/`
- **技术栈**：Vite 5 + React 18 + react-router-dom 6
- **开发端口**：5173（见 `vite.config.js`）
- **构建产物**：`dist/`（静态文件，由 Nginx 托管）
- **后端代理**：
  - `/query-api/*` → 后端查询服务 `svc-query`（端口 8001）
  - `/import-api/*` → 后端导入服务 `svc-ingest`（端口 8000）

### 目录结构

```
web/
├── Dockerfile              # 多阶段构建：node:20-alpine → nginx:alpine
├── nginx.conf.template     # Nginx 配置模板（SPA fallback + 反代占位符）
├── package.json            # 依赖清单，scripts: dev / build / preview
├── package-lock.json       # 依赖锁定文件（npm ci 严格依据此文件安装）
├── vite.config.js          # Vite 配置（开发端口 + 代理规则）
├── index.html              # SPA 入口 HTML
├── src/
│   ├── main.jsx            # React 渲染入口
│   ├── App.jsx             # 顶层路由组件
│   ├── api/                # 后端 API 调用（统一走 /query-api 或 /import-api）
│   │   ├── chat.js
│   │   ├── config.js
│   │   └── importer.js
│   ├── components/         # 可复用组件
│   │   ├── AnswerBody.jsx
│   │   ├── AppLayout.jsx
│   │   ├── ProgressPanel.jsx
│   │   └── RequireAuth.jsx
│   ├── pages/              # 路由页面
│   │   ├── Chat.jsx        # 问答页（主入口）
│   │   ├── Import.jsx      # 文档导入页
│   │   ├── Login.jsx       # 登录页
│   │   └── UserCenter.jsx  # 用户中心
│   ├── styles/
│   │   └── global.css
│   └── utils/              # 工具函数
│       ├── answerParser.js
│       ├── auth.js
│       └── sessions.js
```

---

## 2. 开发环境搭建

前置条件：Node.js 20+、npm 10+。

```bash
# 1. 进入前端目录
cd /coding/aipaas/web

# 2. 安装依赖（严格按 package-lock.json，比 npm install 更稳定可复现）
npm ci

# 3. 启动开发服务器
npm run dev
```

启动后访问 <http://localhost:5173>。开发服务器会自动监听文件变更并热更新。

开发期 API 代理由 `vite.config.js` 内置：

| 前端路径前缀 | 转发目标（开发机） |
| --- | --- |
| `/query-api/*` | `http://127.0.0.1:8001/*` |
| `/import-api/*` | `http://127.0.0.1:8000/*` |

开发时需要后端两个 Python 服务（`svc-query` 端口 8001、`svc-ingest` 端口 8000）在本机运行，否则页面功能无法正常使用。

---

## 3. 生产构建

```bash
cd /coding/aipaas/web

# 构建静态产物到 dist/
npm run build

# 本地预览构建产物（在 dist/ 上起一个静态服务器）
npm run preview
```

`npm run build` 会执行 `vite build`，输出到 `dist/` 目录。构建成功的标志：
- 无红色报错
- `dist/index.html`、`dist/assets/` 下有生成的 `.js` / `.css` 文件

`npm run preview` 启动一个静态服务器（默认端口 4173）用于本地验证产物。

---

## 4. Docker 镜像构建 & 单独运行

### 镜像构建

```bash
cd /coding/aipaas/web
docker build -t aipaas/frontend:latest .
```

该 Dockerfile 是多阶段构建：
1. **build 阶段**（`node:20-alpine`）：`npm ci` → `npm run build`，产出 `dist/`
2. **runtime 阶段**（`nginx:alpine`）：仅携带 `dist/` + `nginx.conf.template`，无 Node.js，镜像体积小

### 单独运行（反代到本机后端）

```bash
docker run -d --name aipaas-frontend -p 80:80 \
  -e QUERY_API_UPSTREAM=http://host.docker.internal:8001 \
  -e IMPORT_API_UPSTREAM=http://host.docker.internal:8000 \
  aipaas/frontend:latest
```

- `host.docker.internal` 是 Docker Desktop 自动注入的特殊 DNS，指向宿主机 IP；Linux 原生 Docker 需加 `--add-host=host.docker.internal:host-gateway`
- 容器会用 `envsubst` 将上述环境变量注入到 Nginx 配置模板里

验证：
```bash
curl http://localhost/health   # 期望返回 ok
docker logs -f aipaas-frontend
docker stop aipaas-frontend && docker rm aipaas-frontend
```

---

## 5. docker compose 中部署

### 一键启动（推荐）

```bash
cd /coding/aipaas/deploy/docker
cp .env.example .env   # 首次使用
docker compose up -d
```

前端服务 `frontend` 定义在主编排 [docker-compose.yaml](../deploy/docker/docker-compose.yaml) 中（主编排 `include` 基础编排 `docker-compose-base.yaml`），全量启动会自动拉起（等待依赖的 svc-ingest / svc-query healthy）。

### 只启前端模块

前端依赖后端 `svc-ingest`（8000）和 `svc-query`（8001）健康。`up -d frontend` 会按 `depends_on` 自动拉起两个 Python 服务及其依赖的中间件：

```bash
docker compose up -d frontend
```

### compose 中前端的环境变量

在 [docker-compose.yaml](../deploy/docker/docker-compose.yaml) 中，前端服务自动注入：

```yaml
environment:
  QUERY_API_UPSTREAM: http://svc-query:8001
  IMPORT_API_UPSTREAM: http://svc-ingest:8000
```

容器内通过 Docker 内部网络（`aipaas-network`）以服务名解析 DNS，无需写主机 IP。

### 查看状态与日志

```bash
docker compose ps frontend
docker compose logs -f frontend        # Nginx access/error 日志
docker compose logs -f frontend --tail=200
```

---

## 6. Nginx 反代机制

容器启动时执行的核心逻辑（见 Dockerfile 末尾的 CMD）：

```sh
# 去掉环境变量末尾可能多余的 "/"，避免 proxy_pass URI 覆盖
QUERY_API_UPSTREAM="${QUERY_API_UPSTREAM%/}"
IMPORT_API_UPSTREAM="${IMPORT_API_UPSTREAM%/}"
export QUERY_API_UPSTREAM IMPORT_API_UPSTREAM

# 用 envsubst 把 nginx.conf.template 里的 ${QUERY_API_UPSTREAM} / ${IMPORT_API_UPSTREAM}
# 替换为运行时环境变量的值，生成 default.conf
envsubst '${QUERY_API_UPSTREAM} ${IMPORT_API_UPSTREAM}' \
  < /etc/nginx/nginx.conf.template \
  > /etc/nginx/conf.d/default.conf

# 前台启动 nginx（daemon off 模式，容器退出会自动停止）
exec nginx -g 'daemon off;'
```

### 三个关键点

1. **`envsubst` 运行时注入**：Nginx 配置里不含硬编码的上游地址，由容器启动时的环境变量决定 → 同一镜像可部署到不同环境。

2. **`resolver 127.0.0.11` + 变量形式 `proxy_pass`**：
   - Docker 内置 DNS 在所有 bridge / compose 网络固定为 `127.0.0.11`
   - 上游地址写成 `set $query_upstream ${QUERY_API_UPSTREAM}; proxy_pass $query_upstream;`，而不是 `proxy_pass http://svc-query:8001;`
   - **效果**：DNS 解析推迟到每个请求发起时（而非容器启动时），避免后端服务尚未就绪就导致 Nginx 报 `[emerg] host not found in upstream` 并崩溃退出

3. **SPA fallback**：
   ```nginx
   location / {
       root   /usr/share/nginx/html;
       try_files $uri $uri/ /index.html;
   }
   ```
   浏览器直接刷新 `/chat`、`/import` 等深层路由时，请求由 Nginx 返回 `index.html`，再由前端 `react-router-dom` 在客户端接管路由。

---

## 7. 日志查看

前端模块本身没有业务层日志，所有日志来自 Nginx：

| 日志类型 | 输出位置 | 查看方式 |
| --- | --- | --- |
| access_log | stdout | `docker logs -f frontend` 或 `docker compose logs -f frontend` |
| error_log | stderr | 同上 |

容器停止后日志随之消失，如需持久化可在 Nginx 配置里把 `access_log` / `error_log` 写到挂载的卷，或开启 Docker 的 log rotation（`docker-compose.yaml` 里的 `x-logging` 锚点已配置 json-file + 50MB/3 文件轮转）。

---

## 8. 新增页面 / 组件 / API 对接规范

### 新增页面

1. 在 `src/pages/` 下新建 `.jsx` 文件，例如 `Dashboard.jsx`
2. 在 `App.jsx` 里用 `react-router-dom` 注册路由：

```jsx
// App.jsx 示例
import Dashboard from './pages/Dashboard';

function App() {
  return (
    <Routes>
      <Route path="/" element={<Chat />} />
      <Route path="/dashboard" element={<Dashboard />} />
      {/* ... */}
    </Routes>
  );
}
```

> 新增路由后，由于 Nginx 的 `try_files $uri $uri/ /index.html` 已做 SPA fallback，不需要修改 Nginx 配置。

### 新增组件

放在 `src/components/`，命名使用大驼峰 `.jsx` 文件，父组件通过 `import` 引用。

### API 对接

所有对后端的请求**必须**走 `/query-api/*` 或 `/import-api/*` 前缀：

- 查询 / 问答类 → 走 `/query-api/*`（对应后端 `svc-query:8001`）
- 文档导入 / 写入类 → 走 `/import-api/*`（对应后端 `svc-ingest:8000`）

```js
// 示例：在 src/api/chat.js 中调用查询服务
export async function askQuestion(prompt) {
  const res = await fetch('/query-api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  });
  return res.json();
}
```

不要在前端代码里硬编码后端 IP 或端口；统一走 `/query-api/xxx` / `/import-api/xxx` 路径，开发时由 Vite 代理、生产时由 Nginx 反代。

### 构建产物验证

每次改动后至少执行一次：

```bash
npm run build
```

确认构建无报错、`dist/` 目录正常产出。

---

## 9. 常见问题（FAQ）

### Q1：`npm ci` 失败

常见原因：
- Node.js 版本过低（要求 20+）
- `package-lock.json` 与 `package.json` 不同步（锁文件不是当前环境生成的）

解决：
```bash
node -v   # 确认 Node 版本
# 如果锁文件损坏，退回 npm install 或重新生成锁文件
rm -rf node_modules package-lock.json
npm install
# 提交新的 package-lock.json
npm install
```

### Q2：开发时 API 请求 404 / 代理不通

- 确认后端 Python 服务在本机运行：
  ```bash
  curl http://127.0.0.1:8001/health   # svc-query
  curl http://127.0.0.1:8000/health   # svc-ingest
  ```
- 确认前端代码里的请求路径前缀正确（`/query-api/xxx` 而非 `/xxx`）
- 确认 `vite.config.js` 未被误改，proxy.target 仍指向 `127.0.0.1:8001` / `127.0.0.1:8000`

### Q3：容器内刷新路由出现 404

典型症状：直接访问 `http://<host>/chat` 正常，但刷新同一路径浏览器得到 Nginx 404 页。

原因：Nginx 配置里没有 SPA fallback。

确认 `nginx.conf.template` 里 `location /` 有：

```nginx
try_files $uri $uri/ /index.html;
```

如果是旧镜像，重新 `docker build` 一次即可。

### Q4：容器启动后找不到后端（`host not found in upstream`）

**反例**：写死 `proxy_pass http://svc-query:8001;`，容器启动时 DNS 无法解析就直接退出。

**正例**（当前项目做法）：`resolver 127.0.0.11` + `set` + 变量形式 `proxy_pass`：

```nginx
resolver 127.0.0.11 valid=10s ipv6=off;

location /query-api/ {
    set $query_upstream ${QUERY_API_UPSTREAM};
    rewrite ^/query-api/(.*)$ /$1 break;
    proxy_pass $query_upstream;
}
```

这样后端服务即使稍后才就绪，容器也能先正常启动，等请求进来时再做 DNS 解析。

### Q5：Linux 本机用 `docker run` 时后端连不上

`host.docker.internal` 在 Linux 原生 Docker 默认未配置。解决：

```bash
docker run -d --name aipaas-frontend -p 80:80 \
  --add-host=host.docker.internal:host-gateway \
  -e QUERY_API_UPSTREAM=http://host.docker.internal:8001 \
  -e IMPORT_API_UPSTREAM=http://host.docker.internal:8000 \
  aipaas/frontend:latest
```

或直接写实际宿主机 IP：

```bash
docker run -d --name aipaas-frontend -p 80:80 \
  -e QUERY_API_UPSTREAM=http://172.17.0.1:8001 \
  -e IMPORT_API_UPSTREAM=http://172.17.0.1:8000 \
  aipaas/frontend:latest
```

### Q6：envsubst 替换后上游地址末尾多了 `/`

Nginx 的 `proxy_pass http://xxx/yyy` 行为与 `http://xxx` 不同（会 rewrite URI）。当前 Dockerfile 的 CMD 已处理：

```sh
QUERY_API_UPSTREAM="${QUERY_API_UPSTREAM%/}"
IMPORT_API_UPSTREAM="${IMPORT_API_UPSTREAM%/}"
```

这两行会去掉环境变量末尾的 `/`，正常情况下无需手动处理。

---

## 附：快速命令速查

```bash
# 开发
cd web && npm ci && npm run dev          # http://localhost:5173
npm run build && npm run preview         # 构建 + 本地预览

# 镜像
cd web
docker build -t aipaas/frontend:latest .
docker run -d -p 80:80 --name aipaas-frontend \
  -e QUERY_API_UPSTREAM=http://host.docker.internal:8001 \
  -e IMPORT_API_UPSTREAM=http://host.docker.internal:8000 \
  aipaas/frontend:latest

# compose
cd deploy/docker
docker compose up -d frontend            # 启动前端（自动拉起依赖）
docker compose logs -f frontend          # 查看 Nginx 日志
docker compose ps frontend               # 状态
docker compose down                      # 停止全部
```
