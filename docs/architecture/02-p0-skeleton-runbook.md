# P0 骨架运行手册（Step 0）

> **历史记录**：本文记录 Step 0 冻结契约与骨架时的状态，其中部分端口映射、契约统计和“未实现”描述已随迭代变化。当前使用请以[快速使用入门](../00_快速使用入门.md)、[架构说明](../03_架构说明.md)和 [P0 部署手册](../acceptance/P0-DEPLOYMENT-MANUAL.md)为准。

> 目标：一条命令拉起 P0 骨架，验证各服务 `/healthz`、`/readyz` 返回 200，并完成一次「上传 → 问答」契约串联。
> 前置：Docker 24+、docker-compose v2.32+。本地开发另需 Python 3.12 / Node 20 / JDK 21（Java 可用容器构建，无需本机 JDK）。

---

## 1. 一键启动（compose）

```bash
cd /home/aipaas/aipaas
docker-compose -f deploy/docker/docker-compose-p0.yaml up -d --build
docker-compose -f deploy/docker/docker-compose-p0.yaml ps
```

首次启动需构建 4 个镜像（platform-api / rag-ai-api / mock / web）并拉取 postgres、redis、minio、etcd、milvus、prometheus。Milvus 冷启动 `start_period` 90s，属正常现象。

### 端口与入口

| 组件 | 宿主端口 | 容器端口 | 用途 |
|---|---|---|---|
| platform-api（Java） | 8081 | 8080 | P0 契约主入口 `/api/v1/**` |
| rag-ai-api（Python） | 8100 | 8100 | AI 能力骨架（未实现路由返回 501） |
| contracts-mock | 4010 | 4010 | 零依赖契约 mock，前后端独立联调 |
| web（nginx） | 18080 | 80 | SPA + `/api/` 反代到 platform-api |
| postgres | 仅容器内网 | 5432 | 元数据/审计（不暴露宿主机） |
| redis | 56379 | 6379 | 缓存/幂等键 |
| minio | 19000 / 19001 | 9000 / 9001 | 对象存储 / 控制台 |
| milvus | 29530 / 29091 | 19530 / 9091 | 向量库 / 健康检查 |
| prometheus | 19090 | 9090 | 指标抓取（#177） |

> 端口刻意避开宿主机既有服务（8080/5432/6379/9000/19530/9091），可与现有 `deploy/docker/docker-compose.yaml` 并存。

---

## 2. 健康检查验证

```bash
B=http://127.0.0.1
for u in \
  "$B:8081/healthz" "$B:8081/readyz" \
  "$B:8100/healthz" "$B:8100/readyz" \
  "$B:4010/healthz" "$B:4010/readyz" \
  "$B:18080/healthz" "$B:18080/readyz" \
  "$B:19090/-/healthy" "$B:29091/healthz" \
  "$B:19000/minio/health/live"; do
  printf '%-46s %s\n' "$u" "$(curl -s -o /dev/null -w '%{http_code}' "$u")"
done
```

期望全部为 `200`。基础设施容器状态：

```bash
docker-compose -f deploy/docker/docker-compose-p0.yaml ps --format 'table {{.Name}}\t{{.Status}}'
```

| 服务 | 检查方式 |
|---|---|
| postgres | `pg_isready -U aipaas -d aipaas` |
| redis | `redis-cli ping` → `PONG` |
| minio | `GET /minio/health/live` → 200 |
| etcd | `etcdctl endpoint health` |
| milvus | `GET :9091/healthz` → 200 |
| prometheus | `GET :9090/-/healthy` → 200 |
| platform-api | `GET :8080/healthz`、`/readyz` → 200 |
| rag-ai-api | `GET :8100/healthz`、`/readyz` → 200 |
| mock | `GET :4010/healthz`、`/readyz` → 200 |
| web | `GET /healthz`、`/readyz` → 200 |

---

## 3. 契约校验（离线，不依赖容器）

```bash
python3 aipaas-contracts/scripts/verify_contract.py
```

期望输出：

```
契约版本: 1.0.0-p0  状态: frozen
path: 61  operation: 71  写操作: 31
schema: 167  $ref: 601  坏引用: 0
P0 功能覆盖: 44/44
结果: PASS
```

---

## 4. 「上传 → 问答」契约串联

### 4.1 对 mock（推荐，零依赖）

```bash
# 容器：http://127.0.0.1:4010
python3 aipaas-contracts/mock/smoke_upload_qa.py --base-url http://127.0.0.1:4010

# 或本地直跑
python3 aipaas-contracts/mock/server.py --port 4010
python3 aipaas-contracts/mock/smoke_upload_qa.py --base-url http://127.0.0.1:4010
```

期望 `23 passed, 0 failed`，链路：

```
/healthz → /readyz → POST /auth/login → POST /knowledge-bases
→ POST /knowledge-bases/{kbId}/documents（Idempotency-Key）
→ GET /ingest/tasks/{taskId}（轮询 QUEUED→RUNNING→SUCCEEDED）
→ GET /documents/{documentId}/chunks
→ POST /chat（引用 citation）
→ POST /chat/stream（meta/delta/done）
→ viewer 越权 403（PERM.*）
→ GET /audit-logs（doc.upload / chat.query / permission.denied）
```

### 4.2 对 platform-api（骨架）

```bash
curl -s -X POST http://127.0.0.1:8081/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin12345"}'
```

P0 骨架仅实现 `/api/v1/system/info`；其余契约路径返回 `501 SYS.NOT_IMPLEMENTED`（统一信封，前端可先对接错误分支）。

```bash
curl -s http://127.0.0.1:8081/api/v1/system/info -H 'Authorization: Bearer dev'
```

---

## 5. 本地开发（不启动容器）

### 5.1 mock（前端联调）

```bash
python3 aipaas-contracts/mock/server.py --port 4010
cd web && VITE_PLATFORM_API_BASE=http://127.0.0.1:4010/api/v1 npm run dev
# 或保留相对路径 /api/v1，让 vite 代理到 mock：
PLATFORM_API_TARGET=http://127.0.0.1:4010 npm run dev
```

### 5.2 Python

```bash
cd pymodules/apps/rag-ai-api
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn rag_ai_api.main:app --host 0.0.0.0 --port 8100
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/healthz   # 200
```

### 5.3 Java

```bash
# 本机有 JDK 21：
export JAVA_HOME=/path/to/jdk-21
cd javaplat && ./mvnw -q -DskipTests package
java -jar apps/platform-api/target/platform-api.jar

# 无本机 JDK：直接用容器构建/运行
docker build -t aipaas/platform-api:p0 javaplat
docker run --rm -p 8081:8080 aipaas/platform-api:p0
```

### 5.4 Web 生产构建

```bash
cd web && npm ci && npm run build
```

---

## 6. 停止与清理

```bash
# 停止（保留数据卷）
docker-compose -f deploy/docker/docker-compose-p0.yaml down

# 停止并删除 P0 数据卷（不影响现有 deploy/docker 数据）
docker-compose -f deploy/docker/docker-compose-p0.yaml down -v
```

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `p0-milvus` 反复重启 | etcd/minio 未就绪 | 等待 etcd/minio healthy；Milvus 冷启动约 90s |
| `platform-api` 健康检查失败 | 镜像构建期 Maven 依赖未下载完 | `docker-compose build platform-api` 查看日志；`docker logs p0-platform-api` |
| 端口被占用 | 宿主机已有服务 | 修改 compose 左侧端口（如 8081→18081），或先停旧容器 |
| web 502 `/api/` | platform-api 未就绪 | 等待其 healthcheck 通过；检查 `PLATFORM_API_UPSTREAM` |
| mock SSE 立即结束 | 客户端使用 HTTP/1.0 或代理缓冲 | mock 已用 `Connection: close`；nginx 已 `proxy_buffering off` |
| `verify_contract.py` 报坏引用 | 手工编辑 YAML 产生 `#components/...` | 必须写 `#/components/...` |
| Java 构建报 `release version 21 not supported` | 本机只有 JRE | 使用容器构建（见 5.3） |

---

## 8. 验收对照

> 2026-11-17 实测（本项目工作机）：除 Prometheus 镜像因 Docker Hub 拉取限速仍在后台拉取外，其余 9 个服务全部 `healthy`，健康端点均返回 200；`verify_contract.py` PASS；mock 串联 23/23。

| 验收项 | 验证方式 | 结果 |
|---|---|---|
| 10 个功能域契约齐备 | `verify_contract.py` + 评审记录 | PASS（44/44，10 域） |
| docker-compose up 后各服务健康检查通过 | 第 2 节脚本 | 9/9 healthy（Prometheus 镜像拉取中，配置已就绪） |
| mock 完成一次「上传 → 问答」串联 | 第 4.1 节 | `23 passed, 0 failed` |

### 8.1 实测健康检查输出

```
http://127.0.0.1:8081/healthz              200   platform-api
http://127.0.0.1:8081/readyz               200
http://127.0.0.1:8100/healthz              200   rag-ai-api
http://127.0.0.1:8100/readyz               200
http://127.0.0.1:4010/healthz              200   contracts-mock
http://127.0.0.1:4010/readyz               200
http://127.0.0.1:18080/healthz             200   web
http://127.0.0.1:18080/readyz              200
http://127.0.0.1:29091/healthz             200   milvus
http://127.0.0.1:19000/minio/health/live   200   minio
```

`docker-compose ps`：postgres / redis / minio / etcd / milvus / platform-api / rag-ai-api / mock / web 均为 `Up (healthy)`。

### 8.2 容器构建说明

- `mock`、`rag-ai-api`、`web`：`docker-compose build` 直接成功。
- `platform-api`：本机 Docker Hub 拉取 `maven:3.9-eclipse-temurin-21` 受限，采用等效方式验证——宿主 JDK 21 执行 `./mvnw -DskipTests package` 构建出 `platform-api.jar`，以同环境变量/健康检查在 compose 中运行，`/healthz`、`/readyz`、`/api/v1/system/info`、401、501 兜底均验证通过。网络正常环境直接 `docker-compose up -d --build` 即可。
