# AIPaaS Java 微服务开发部署文档

> ⚠️ **状态声明：本文档已废弃（被网关+管理拆分方案替代）。** 本文描述的 gateway / svc-auth /
> svc-document / svc-search / svc-agent 五微服务规划**不再是目标架构**，仅保留作历史参考，
> 正文中的模块拆解、端口、Docker 构建与 compose 命令均为已废弃的早期规划，不得据此实施。
> 当前 Java 侧只有 `platform-gateway` 与 `platform-management` 两个可部署应用，共享 `libs/common`；
> 真实结构与部署方式以 [`docs/../ai-programming/Java网关与管理拆分实施提示词.md`](../05_AI编程/01_Java网关与管理拆分实施提示词.md)
> 和 `javaplat/pom.xml`、`deploy/docker/docker-compose-java.yaml` 为准。
>
> 文档范围（历史）：`javaplat/` 目录下早期规划的 5 个 Java 微服务（Spring Boot 21）
> 适用场景（历史）：本地开发调试 / Docker 镜像构建 / docker compose 部署

---

## 1. 模块简介

`javaplat/` 目录规划承载 AIPaaS 后端 Java 微服务，目前处于**规划状态**：源码（`pom.xml` + `src/`）尚未补齐，Dockerfile 未落地（此前预创建的 5 个 Dockerfile 已删除，避免与最终方案冲突），也未纳入 `deploy/docker/docker-compose.yaml` 编排。模块功能拆解与评审确认点见 `javaplat/TODO说明.md`。

### 服务职责

| 服务 | 技术栈 | 容器内部端口 | 主要职责 |
|------|--------|-------------|---------|
| `gateway` | Spring Cloud Gateway | 8080 | API 网关、路由转发、统一鉴权入口 |
| `svc-auth` | Spring Boot | 8081 | 用户认证、令牌管理、权限校验 |
| `svc-document` | Spring Boot | 8082 | 文档上传/解析、与 MinIO 和 Python 摄入服务协作 |
| `svc-search` | Spring Boot | 8083 | 检索查询、结果聚合 |
| `svc-agent` | Spring Boot | 8084 | Agent 编排、对话管理 |

### 依赖关系

```
gateway ─────────────┬──► svc-auth
                     ├──► svc-document ──► minio / rocketmq-namesrv / app-ingest-worker(Python)
                     ├──► svc-search
                     └──► svc-agent

所有 Java 服务 ──► nacos（配置中心 + 注册中心）
所有 Java 服务 ──► postgres（业务库）
所有 Java 服务 ──► redis（缓存 + 会话）
```

### 端口总览（容器内部）

| 服务 | 内部端口 | compose 默认宿主机映射 | 说明 |
|------|---------|----------------------|------|
| gateway | 8080 | 8085 | API 网关入口 |
| svc-auth | 8081 | 8081 | — |
| svc-document | 8082 | 9082 | — |
| svc-search | 8083 | 8083 | — |
| svc-agent | 8084 | 8086 | — |

> 宿主机端口变量已在 `deploy/docker/.env.example` 的 Java 微服务区段预留（当前为注释状态），落地时取消注释；生产部署可自定义。

---

## 2. 开发环境准备

> 注：以下步骤在 **源码补齐后**（即 `javaplat/` 下有 `pom.xml` 和各子模块 `src/`）执行，当前**待实现**。

### 前置要求

- **JDK 21**：建议 Eclipse Temurin（与 Dockerfile 基础镜像一致）
- **Maven 3.9+**
- **Nacos 配置中心**：Java 服务全部从 Nacos 拉配置，需预先启动
- **PostgreSQL / Redis / MinIO / RocketMQ**：按需启动，完整依赖见上文依赖关系

### Maven 构建

```bash
# 进入 javaplat 目录
cd javaplat

# 方式 A：一次构建所有模块（跳过测试）
mvn clean package -DskipTests

# 方式 B：只构建某一个模块（-am 会同时构建依赖模块）
mvn -pl gateway     -am clean package -DskipTests
mvn -pl svc-auth    -am clean package -DskipTests
mvn -pl svc-document -am clean package -DskipTests
mvn -pl svc-search  -am clean package -DskipTests
mvn -pl svc-agent   -am clean package -DskipTests
```

构建产物位于各模块的 `target/*.jar`，例如 `javaplat/gateway/target/gateway.jar`。

### Nacos 配置准备

在 Nacos 控制台或 API 中为每个服务创建配置文件，命名约定：

```
{application-name}-{profile}.yaml
```

例如：

- `gateway-dev.yaml`
- `svc-auth-dev.yaml`
- `svc-document-dev.yaml`
- `svc-search-dev.yaml`
- `svc-agent-dev.yaml`

数据库/Redis/MinIO/RocketMQ 等中间件地址统一写在 Nacos 配置里，容器内通过服务名（`nacos`、`postgres`、`redis` 等）通信。

---

## 3. Docker 镜像构建 & 单独运行（待实现）

> ⚠️ 对应的 Dockerfile（`javaplat/<module>/Dockerfile`）已删除，以下命令与示例在落地前**不可执行**，仅作为规划参考；落地后按第 5 节的统一模板约定重建 Dockerfile 再使用。

### 镜像构建

构建上下文统一是 `javaplat/` 根目录。统一模板中的 `COPY target/*.jar /app/app.jar` 会从构建上下文根目录找 `target/` 下的 jar。

```bash
cd javaplat

# —— 假设已执行 mvn package，jar 已在各模块 target/*.jar ——
# 需要把所有模块 jar 聚合到 javaplat/target/ 下才能匹配 COPY target/*.jar
# （多模块工程可在父 pom.xml 配置 maven-assembly-plugin，或手动 cp）

# 构建全部镜像
docker build -f gateway/Dockerfile      -t aipaas/gateway:latest .
docker build -f svc-auth/Dockerfile     -t aipaas/svc-auth:latest .
docker build -f svc-document/Dockerfile -t aipaas/svc-document:latest .
docker build -f svc-search/Dockerfile   -t aipaas/svc-search:latest .
docker build -f svc-agent/Dockerfile    -t aipaas/svc-agent:latest .
```

### 单独运行示例（待实现）

> Nacos / Postgres / Redis 必须已就绪并在同一网络。

```bash
# 创建独立网络（如果不放到 compose 里）
docker network create aipaas-network

# 启动 gateway
docker run -d --name gateway --network aipaas-network \
  -p 8085:8080 \
  -e NACOS_HOST=nacos -e NACOS_PORT=8848 \
  -e REDIS_HOST=redis -e REDIS_PORT=6379 \
  -v /tmp/logs/gateway:/app/logs \
  aipaas/gateway:latest

# 启动 svc-auth（还需要 postgres）
docker run -d --name svc-auth --network aipaas-network \
  -p 8081:8081 \
  -e NACOS_HOST=nacos -e NACOS_PORT=8848 \
  -e POSTGRES_HOST=postgres \
  -e REDIS_HOST=redis \
  -v /tmp/logs/svc-auth:/app/logs \
  aipaas/svc-auth:latest

# 启动 svc-document（依赖最全：nacos + postgres + redis + minio + Python 服务）
docker run -d --name svc-document --network aipaas-network \
  -p 9082:8082 \
  -e NACOS_HOST=nacos -e NACOS_PORT=8848 \
  -e POSTGRES_HOST=postgres \
  -e REDIS_HOST=redis \
  -e MINIO_ENDPOINT=http://minio:9000 \
  -e ROCKETMQ_HOST=rocketmq-namesrv \
  -e PYTHON_SERVICE_URL=http://app-ingest-worker:8000 \
  -v /tmp/logs/svc-document:/app/logs \
  aipaas/svc-document:latest
```

### 健康检查验证

```bash
curl http://localhost:8085/actuator/health   # gateway
curl http://localhost:8081/actuator/health   # svc-auth
# ...
```

---

## 4. docker compose 中部署（Java 服务条目待实现）

> ⚠️ Java 微服务尚未纳入编排（`javaplat` 规划状态）。当前 `docker compose up -d` 只会启动中间件 + Python 服务 + 前端，**不存在任何 Java 服务条目**。
>
> 落地后将在主编排 `deploy/docker/docker-compose.yaml` 中新增对应服务条目（build context `../../javaplat`，depends_on nacos/postgres/redis 均 `service_healthy`），并统一用 `docker compose up -d <服务名>` 启动（profiles 机制已删除）。

### 当前可用的命令（中间件调试）

Java 服务开发调试所需的平台中间件已包含在主编排中，可提前启动：

```bash
cd deploy/docker

# 0. 拷贝环境变量模板（首次）
cp .env.example .env
vim .env   # 生产环境必须修改密码

# 1. 启动 Java 服务依赖的平台中间件（postgres / redis / nacos / rocketmq）
docker compose up -d postgres redis nacos rocketmq-namesrv rocketmq-broker

# 2. 查看状态
docker compose ps

# 3. 查看日志
docker compose logs -f nacos

# 4. 停止全部
docker compose down
```

（如还需 base 编排的基础中间件 milvus / minio / mongodb，按需执行 `docker compose up -d milvus minio mongodb`。）

### Java 服务启动顺序（规划）

落地后完整依赖链如下（由 compose 的 `depends_on.condition: service_healthy` 保证）：

```
init-volumes
  └─► postgres ──► nacos
  └─► redis
  └─► minio
  └─► rocketmq-namesrv ──► rocketmq-broker
  └─► milvus（etcd + minio）  [Python 依赖]
  └─► mongodb                 [Python 依赖]

中间件就绪后：
  app-ingest-worker(Python) ──► svc-document(Java)
  nacos + postgres + redis ──► gateway / svc-auth / svc-search / svc-agent
  svc-document 还额外等 minio + rocketmq-namesrv + app-ingest-worker
```

### 常用命令（Java 服务落地后适用）

```bash
cd deploy/docker

# 启动指定的 Java 服务（depends_on 自动拉起依赖的中间件）
docker compose up -d gateway svc-auth

# 查看状态 / 日志
docker compose ps
docker compose logs -f gateway
docker compose logs -f svc-document

# 停止全部
docker compose down
```

### 各 Java 服务的 depends_on（规划）

| 服务 | 依赖服务 | condition |
|------|---------|-----------|
| gateway | nacos, redis | `service_healthy` |
| svc-auth | nacos, postgres, redis | `service_healthy` |
| svc-search | nacos, postgres, redis | `service_healthy` |
| svc-agent | nacos, postgres, redis | `service_healthy` |
| svc-document | nacos, postgres, redis, minio, rocketmq-namesrv, app-ingest-worker | `service_healthy` |

> **关键**：所有依赖都使用 `service_healthy`，而不是 `service_started`。这能避免 Java 服务在 Nacos/Postgres 还没完全就绪时就启动导致连接失败。

### build 上下文（规划）

落地后 `docker-compose.yaml` 中 Java 服务的 `build` 配置：

```yaml
gateway:
  build:
    context: ../../javaplat
    dockerfile: gateway/Dockerfile
```

构建上下文是 `deploy/docker/../../javaplat`，即 `javaplat/` 根目录。Dockerfile 内的 `COPY target/*.jar /app/app.jar` 从这个上下文找 jar。

---

## 5. 规划中的统一 Dockerfile 模板约定

> 5 个 Dockerfile 文件已删除，尚未创建。以下为评审中的**统一模板约定**，落地时每个模块按此模板新建 `javaplat/<module>/Dockerfile`（见 `javaplat/TODO说明.md` 第 3 节），仅 `LOG_FILE_PREFIX` 和 `EXPOSE` 端口不同。下面以 gateway 为例示意。

```dockerfile
FROM eclipse-temurin:21-jre-alpine AS runtime

ENV JAVA_OPTS="-Xms256m -Xmx512m -XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0" \
    LOG_DIR=/app/logs \
    LOG_FILE_PREFIX=gateway

WORKDIR /app

RUN mkdir -p /app/logs
VOLUME ["/app/logs"]

COPY target/*.jar /app/app.jar
# 独立模块构建时改成：COPY gateway/target/*.jar /app/app.jar

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD wget -q -O- http://localhost:8080/actuator/health || exit 1

ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

### 基础镜像选择（规划约定）

- **eclipse-temurin:21-jre-alpine** — JRE 足够运行 jar，比完整 JDK 镜像小很多（约 100MB vs 300MB+）
- Alpine 体积小、启动快、安全漏洞面窄
- Temurin 是 Eclipse 官方维护的 OpenJDK 发行版，稳定可靠

### JAVA_OPTS 默认值（规划约定）

| 参数 | 说明 |
|------|------|
| `-Xms256m -Xmx512m` | 堆内存上下限，可按需覆盖 |
| `-XX:+UseContainerSupport` | 让 JVM 感知 cgroup 内存限制 |
| `-XX:MaxRAMPercentage=75.0` | JVM 最多用容器内存的 75%，配合 docker 资源限制 |

运行时可通过环境变量覆盖：

```bash
docker run -e JAVA_OPTS="-Xms512m -Xmx1024m" ...
```

### 日志配置（规划约定）

- `LOG_DIR=/app/logs` — 容器内日志目录
- `LOG_FILE_PREFIX=gateway` — 日志文件名前缀，logback 中可引用
- `VOLUME ["/app/logs"]` — 声明为挂载点，compose 中统一挂到宿主机日志目录

### COPY target/*.jar 的两种模式（规划约定）

**模式 A（默认）**：构建上下文是 `javaplat/` 根目录，所有模块的 jar 聚合到根目录的 `target/` 下：

```dockerfile
COPY target/*.jar /app/app.jar
```

适合多模块整体构建 + 统一打包的场景。

**模式 B（独立模块）**：构建上下文仍然是 `javaplat/`，但直接从子模块自己的 `target/` 拷贝：

```dockerfile
COPY gateway/target/*.jar /app/app.jar
```

适合各模块独立构建和发布。落地时在模板中保留两行写法（其一注释），切换时把其中一行注释掉即可。

### 健康检查（规划约定）

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD wget -q -O- http://localhost:8080/actuator/health || exit 1
```

- Alpine 镜像自带 `wget`，无需额外安装
- Spring Boot Actuator 的 `health` 端点返回 200 即为健康
- `start-period=30s` 给 JVM 冷启动留缓冲时间

---

## 6. 日志管理

### 宿主机目录结构（规划）

Java 服务落地后，compose 中通过 `DOCKER_LOG_DIRECTORY` 统一管理：

```yaml
volumes:
  - ${DOCKER_LOG_DIRECTORY:-.}/logs/gateway:/app/logs
  - ${DOCKER_LOG_DIRECTORY:-.}/logs/svc-auth:/app/logs
  # ...
```

> 注意：主编排当前的 `init-app-dirs` 只预创建 `logs/app-ingest-worker`、`logs/app-query-worker`；Java 服务落地时需把各自日志子目录追加进 `init-app-dirs` 的 `mkdir -p` 列表（见第 8.5 节）。

最终宿主机目录结构：

```
DOCKER_LOG_DIRECTORY/
├── gateway/
├── svc-auth/
├── svc-document/
├── svc-search/
└── svc-agent/
```

### logback 配置建议（Spring Boot 内置）

在 Nacos 配置文件中统一配置 logback，利用 `LOG_FILE_PREFIX` 环境变量：

```yaml
# Nacos 配置示例
logging:
  file:
    name: /app/logs/${LOG_FILE_PREFIX}.log
  logback:
    rollingpolicy:
      max-file-size: 100MB
      max-history: 30
      total-size-cap: 3GB
      file-name-pattern: /app/logs/${LOG_FILE_PREFIX}.%d{yyyy-MM-dd}.%i.log.gz
```

这样每个服务的日志文件会滚动归档：

```
/app/logs/gateway.log
/app/logs/gateway.2026-11-10.0.log.gz
/app/logs/gateway.2026-11-10.1.log.gz
```

### 容器标准输出

compose 还配置了容器 JSON 日志驱动：

```yaml
x-logging: &default-logging
  driver: json-file
  options:
    max-size: "50m"
    max-file: "3"
```

应用日志若打印到 stdout/stderr，可用 `docker logs` 查看：

```bash
docker compose logs -f gateway
docker logs --since "10m" aipaas-gateway
```

---

## 7. Nacos 配置说明

### 配置文件命名约定

Spring Cloud Alibaba Nacos 推荐：

```
{application-name}-{profile}.yaml
```

其中 `application-name` 即 `spring.application.name`，`profile` 如 `dev` / `test` / `prod`。

示例：

| 服务 | spring.application.name | 配置文件名 |
|------|------------------------|-----------|
| gateway | gateway | gateway-dev.yaml |
| svc-auth | svc-auth | svc-auth-dev.yaml |
| svc-document | svc-document | svc-document-dev.yaml |
| svc-search | svc-search | svc-search-dev.yaml |
| svc-agent | svc-agent | svc-agent-dev.yaml |

### bootstrap.yaml（服务内配置）

src/main/resources/bootstrap.yaml 里只写最少的 Nacos 连接信息：

```yaml
spring:
  application:
    name: svc-auth
  cloud:
    nacos:
      discovery:
        server-addr: ${NACOS_HOST:nacos}:${NACOS_PORT:8848}
        namespace: ${NACOS_NAMESPACE:public}
      config:
        server-addr: ${NACOS_HOST:nacos}:${NACOS_PORT:8848}
        namespace: ${NACOS_NAMESPACE:public}
        file-extension: yaml
```

其余所有中间件地址、业务配置都放到 Nacos 的 `{application-name}-dev.yaml` 里。

### Nacos 中配置中间件地址示例

```yaml
# svc-auth-dev.yaml
spring:
  datasource:
    url: jdbc:postgresql://${POSTGRES_HOST:postgres}:5432/finrag4j_auth
    username: naco# 这些环境变量来自 docker compose environment 段
  data:
    redis:
      host: ${REDIS_HOST:redis}
      port: 6379
```

> compose 中 Java 服务通过 `environment:` 把 `NACOS_HOST`、`POSTGRES_HOST`、`REDIS_HOST` 等以容器服务名形式注入，Nacos 配置里再用 `${VAR:default}` 兜底。

---

## 8. 新增微服务规范（规划）

新增一个 Java 微服务需要改动以下 **5 处**（均在落地评审通过后执行，详见 `javaplat/TODO说明.md`）：

### 8.1 Maven 多模块 pom.xml

在 `javaplat/pom.xml` 的 `<modules>` 中添加新子模块：

```xml
<modules>
    <module>gateway</module>
    <module>svc-auth</module>
    <module>svc-document</module>
    <module>svc-search</module>
    <module>svc-agent</module>
    <module>svc-xxx</module>   <!-- 新增 -->
</modules>
```

### 8.2 新建 Dockerfile（规划模板）

在 `javaplat/svc-xxx/Dockerfile` 放置：

```dockerfile
FROM eclipse-temurin:21-jre-alpine AS runtime

ENV JAVA_OPTS="-Xms256m -Xmx512m -XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0" \
    LOG_DIR=/app/logs \
    LOG_FILE_PREFIX=svc-xxx

WORKDIR /app

RUN mkdir -p /app/logs
VOLUME ["/app/logs"]

COPY target/*.jar /app/app.jar
# 独立模块构建时改用：
# COPY svc-xxx/target/*.jar /app/app.jar

EXPOSE 8085   # ← 修改为新服务内部端口

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD wget -q -O- http://localhost:8085/actuator/health || exit 1   # ← 端口

ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

### 8.3 docker-compose.yaml 新增服务条目

在 `deploy/docker/docker-compose.yaml` 的 services 下添加：

```yaml
  svc-xxx:
    build:
      context: ../../javaplat
      dockerfile: svc-xxx/Dockerfile
    image: aipaas/svc-xxx:latest
    container_name: aipaas-svc-xxx
    env_file: .env
    environment:
      NACOS_HOST: nacos
      NACOS_PORT: "8848"
      POSTGRES_HOST: postgres
      POSTGRES_PORT: "5432"
      POSTGRES_DB: finrag4j_xxx
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      TZ: Asia/Shanghai
    ports:
      - "${XXX_PORT:-8087}:8085"   # 宿主机端口可自定义
    volumes:
      - ${DOCKER_LOG_DIRECTORY:-.}/logs/svc-xxx:/app/logs
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O- http://localhost:8085/actuator/health || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 60s
    depends_on:
      nacos:
        condition: service_healthy
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    <<: [*default-restart, *default-logging]
```

### 8.4 .env.example 新增端口变量

在 `deploy/docker/.env.example` 的 Java 微服务区段添加（该区段当前为注释状态，落地时取消注释并补充）：

```bash
# --- XXX 服务 ---
XXX_PORT=8087
```

### 8.5 init-app-dirs 日志目录

在主编排 `docker-compose.yaml` 的 `init-app-dirs` command 中追加新服务日志子目录：

```yaml
command: >
  sh -c "mkdir -p /logs/app-ingest-worker /logs/app-query-worker ... /logs/svc-xxx
    && chown -R ${HOST_UID:-1000}:${HOST_GID:-1000} /logs"
```

### 8.6 Nacos 注册 & 健康检查

- 在 Nacos 控制台创建 `svc-xxx-dev.yaml` 配置
- 新服务必须实现 Spring Boot Actuator 依赖和 `/actuator/health` 端点
- 服务启动时会自动通过 Nacos Discovery 注册自己
- 所有服务必须加入同一个 `aipaas-network`，用 service name 互相通信

---

## 9. 常见问题

> 以下问题排查在 Java 服务**落地后适用**（当前 javaplat 为规划状态，相关 Dockerfile / compose 条目尚未创建）。

### COPY target/*.jar 失败

**现象**：`docker build` 报错 `COPY failed: no source files`

**原因**：通配符 `target/*.jar` 没匹配到任何文件

**排查**：

```bash
cd javaplat

# 1. 确认 jar 是否存在
find . -name "*.jar" -not -path "*/.m2/*"

# 2. 如果多模块 jar 分散在各子模块 target/ 下，需要聚合到根目录 target/
mkdir -p target
cp gateway/target/*.jar target/           # 暂时测试
# 或者切换 Dockerfile 中的 COPY 模式（见第 5 节）
```

### Nacos 连接失败

**现象**：Java 服务日志里大量 `Connection refused`、`fail to connect` Nacos 地址

**排查**：

1. 确认 `depends_on.condition` 是 `service_healthy`（不是 `service_started`）
2. 确认 Nacos 健康检查通过：`docker compose ps nacos`
3. 确认 `NACOS_HOST` 用的是 compose 里的服务名 `nacos`，而不是 `localhost` 或 IP
4. 确认 Nacos 数据库已初始化（`../nacos/nacos.sql` + `../sql/` 下的 SQL）

### 容器内无法访问其他服务

**现象**：`ping nacos` 或 `curl http://nacos:8848` 不通

**排查**：

```bash
# 进入容器测试
docker exec -it aipaas-gateway sh
wget -q -O- http://nacos:8848/nacos/v1/console/health/liveness

# 确认所有服务都在同一网络
docker network inspect aipaas-network
```

### 内存溢出 (OOM)

**现象**：容器日志 `java.lang.OutOfMemoryError` 或被 OOM Killer 杀掉

**排查 & 解决方案**：

```bash
# 1. 临时加大 JVM 堆
docker run -e JAVA_OPTS="-Xms512m -Xmx1024m -XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0" ...

# 2. 确认 docker compose 是否设了内存限制
# docker-compose.yaml 里加 deploy.resources.limits.memory: 1g

# 3. -XX:+UseContainerSupport 必须保留，让 JVM 感知 cgroup
```

### /actuator/health 返回 404

**现象**：健康检查一直失败，`curl http://localhost:8080/actuator/health` 返回 404

**原因**：Spring Boot Actuator 依赖没加

**解决**：在 `pom.xml` 中添加：

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
```

并在 Nacos 配置中暴露 health 端点：

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info
  endpoint:
    health:
      show-details: always
```

### 时区问题

默认 Alpine 容器是 UTC，日志时间比北京时间晚 8 小时。在 Dockerfile 中取消注释时区设置行即可：

```dockerfile
RUN apk add --no-cache tzdata && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    apk del tzdata
```

compose 环境变量中已统一注入 `TZ: Asia/Shanghai`。
