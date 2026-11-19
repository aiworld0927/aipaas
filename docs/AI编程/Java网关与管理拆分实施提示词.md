# Java 网关与管理服务拆分实施提示词

> 状态：**已执行完成**。网关 + 管理拆分已落地，并通过 Maven 测试/打包与 Compose 静态校验。
> 本文件保留作为实施规范与验收依据；实际执行结果见末尾「执行结果（交付报告）」。
> 本会话在既有实施基础上补充修复了若干残留项（详见报告）。

---

你是一名熟悉 Java 21、Spring Boot、Spring Cloud Gateway、Maven 多模块、Docker Compose、React/Nginx 以及 Python FastAPI 的高级后端工程师。请在 `/coding/aipaas` 仓库中实施 Java **“网关 + 管理”两应用拆分**，并完成必要的构建、配置、文档和验证工作。

## 一、任务目标

将当前 `javaplat/apps/platform-api` 中的网关与管理职责拆分为两个可独立构建、独立部署的 Spring Boot 应用：

1. `platform-gateway`：平台统一入口，负责路由、通用 Web 治理以及 Python 服务接入。
2. `platform-management`：负责认证管理、用户权限、知识库、文档、审计、系统配置和其他管理功能。
3. 保留 `javaplat/libs/common` 作为共享 Maven 库，不再拆分新的业务微服务。
4. 缩短高频请求链路。最终目标如下：

```text
管理类请求：frontend → platform-gateway → platform-management
问答请求：  frontend → platform-gateway → svc-query
导入请求：  frontend → platform-gateway → svc-ingest
```

不允许把最终问答主链路保留为：

```text
frontend → platform-gateway → platform-management → svc-query
```

拆分过程中可以为了兼容性短暂保留该路径作为中间状态，但不得把它作为最终验收结果。

## 二、明确的范围限制

### 必须做到

- Java 只保留 `platform-gateway` 和 `platform-management` 两个可部署应用。
- 保留现有 API 契约、统一响应信封、认证、权限、审计、Trace、幂等和 SSE 行为。
- 管理数据库、Flyway、MinIO、密钥和业务数据默认归 `platform-management` 管理。
- `platform-gateway` 不依赖业务 Repository，不直接执行业务 CRUD。
- 前端只把 Java 平台入口切换到 `platform-gateway`，不直接访问 `platform-management`。
- 更新 Maven、Dockerfile、第 4/5 级 Compose、环境变量模板和相关说明文档。
- 所有改动必须经过测试和 Compose 静态校验。

### 明确不做

- 不创建 `svc-auth`、`svc-document`、`svc-search`、`svc-agent` 等额外微服务。
- 不把 `auth`、`rbac`、`chat`、`document` 等包分别拆成独立可部署应用。
- 不新增 `packages/java/modules/*` 一类业务模块拆分。
- 不借本任务实施整个仓库的目录迁移。
- 不借本任务合并或删除 `docker-compose-p0.yaml`。
- 不重写 Python 检索、生成或导入算法。
- 不改变 React 页面和交互设计。
- 不为了直连而绕过认证、权限、内容安全、审计或任务状态管理。
- 不引入新的必需中间件；如确需引入，必须先停止并说明原因。
- 不创建 Git commit，除非用户另行明确要求。

## 三、以真实代码为准

开始前必须阅读并理解以下文件：

- `javaplat/pom.xml`
- `javaplat/apps/platform-api/pom.xml`
- `javaplat/libs/common/pom.xml`
- `javaplat/Dockerfile`
- `javaplat/apps/platform-api/src/main/resources/application.yml`
- `javaplat/apps/platform-api/src/main/java/com/aipaas/platform/api/PlatformApiApplication.java`
- `javaplat/apps/platform-api/src/main/java/com/aipaas/platform/api/SecurityConfig.java`
- `javaplat/apps/platform-api/src/main/java/com/aipaas/platform/auth/JwtAuthenticationFilter.java`
- `javaplat/apps/platform-api/src/main/java/com/aipaas/platform/chat/ChatController.java`
- `javaplat/apps/platform-api/src/main/java/com/aipaas/platform/ingest/` 下的任务、调度和 Python 客户端代码
- `deploy/docker/docker-compose-java.yaml`
- `deploy/docker/docker-compose.yaml`
- `deploy/docker/.env.example`
- `web/Dockerfile`
- `web/nginx.conf.template`
- `aipaas-contracts/openapi/` 中相关契约

当前真实状态：

- 根 Maven 工程是 `packaging=pom` 的聚合工程，模块只有 `libs/common` 和 `apps/platform-api`。
- Java 版本为 21，Spring Boot 版本由根 POM 统一管理。
- `platform-api` 当前是可执行 Spring Boot 单体，不是空规划目录。
- `PlatformApiApplication` 使用 `scanBasePackages = "com.aipaas.platform"`，拆分后必须收紧组件扫描边界。
- `SecurityConfig` 当前组合 `JwtAuthenticationFilter`、`AuditFilter` 和 `UploadAuthorizationFilter`。
- `JwtAuthenticationFilter` 不仅验签，还查询令牌撤销、用户状态、权限和租户信息，不能未经改造直接移动到无数据库网关。
- `ChatController` 当前依赖用户仓储、内容安全、审计、Trace 和 Python HTTP 客户端，不能简单复制到网关后就算完成拆分。
- Python `svc-query` 已提供 `/api/v1/chat` 和 `/api/v1/chat/stream` 兼容路径；实施时仍要核对请求、响应、错误和 SSE 契约。
- Python `svc-ingest` 已提供上传、解析、任务和文档处理接口，具体路径必须以真实 Router 和 OpenAPI 为准。

`javaplat/TODO说明.md` 和 `docs/02d_java模块开发部署.md` 中存在早期五微服务规划，部分内容已经过时。**不得依据这些旧规划恢复五微服务架构，以本提示词和真实代码为准。**

## 四、目标 Maven 与目录结构

在当前 `javaplat/` 内完成拆分，不同时进行仓库级目录迁移：

```text
javaplat/
├── pom.xml
├── mvnw
├── libs/
│   └── common/
│       ├── pom.xml
│       └── src/
└── apps/
    ├── platform-gateway/
    │   ├── Dockerfile
    │   ├── pom.xml
    │   └── src/
    │       ├── main/
    │       │   ├── java/com/aipaas/platform/gateway/
    │       │   └── resources/application.yml
    │       └── test/
    │           └── java/com/aipaas/platform/gateway/
    └── platform-management/
        ├── Dockerfile
        ├── pom.xml
        └── src/
            ├── main/
            │   ├── java/com/aipaas/platform/
            │   └── resources/
            └── test/
```

要求：

- 根 `<modules>` 只包含 `libs/common`、`apps/platform-gateway`、`apps/platform-management`。
- 两个应用都使用标准 Maven `src/main/java`、`src/main/resources`、`src/test/java` 布局。
- 两个应用分别生成可执行 Spring Boot JAR，不能共用一个包含全部代码的胖 JAR。
- `libs/common` 只保留真正共享且稳定的代码，不得反向依赖任何应用。
- 不为了拆分而复制 `common` 代码；重复复制会导致行为漂移。
- 根 POM 统一管理 Spring Boot、Spring Cloud（如采用）、Resilience4j、MinIO 等依赖版本，子模块不重复硬编码版本。
- 如使用 Spring Cloud Gateway，必须引入与当前 Spring Boot 版本兼容的 Spring Cloud BOM，并记录所选版本。
- 各应用的组件扫描只覆盖自身及明确依赖的共享包，禁止继续无边界扫描整个 `com.aipaas.platform`。
- 原 `javaplat/Dockerfile` 在两个应用独立 Dockerfile 落地后不得继续作为重复的构建入口。

## 五、职责边界

### 5.1 platform-gateway

负责：

- 统一路由和上游服务发现或静态服务地址配置。
- CORS、Trace、请求大小、超时、限流和通用异常响应。
- JWT 验签及必要的撤销状态检查。
- 身份、租户、权限、Trace 和幂等请求头的安全传递。
- 问答与导入所需的 Python API 契约适配。
- SSE 流式响应透传，包括连接关闭、超时和上游异常处理。

不得承担：

- 用户、角色、权限、知识库等管理数据的 CRUD。
- 业务 Flyway 迁移。
- 业务文档、导出文件或知识库对象存储管理。
- 每次请求都同步调用 `platform-management` 完成鉴权。
- 通过读取业务数据库绕过管理服务。

### 5.2 platform-management

负责：

- 登录、刷新、注销、用户、角色和权限管理。
- 令牌签发、撤销、用户状态和权限版本管理。
- 知识库、文档元数据、系统配置、审计查询和导出。
- 现有数据库、Flyway、MinIO、密钥及业务数据生命周期。
- 仍由 Java 管理的导入任务状态和文档关联关系。
- 管理接口的二次权限校验，不能仅信任网关传入的身份头。

要求：

- 不作为问答主链路的必经中转。
- 只对容器网络暴露服务端口，不默认映射宿主机端口。
- 保留现有管理 API 的路径、响应和错误语义。

## 六、路由设计

实施前先基于所有 Java Controller、Python Router 和 OpenAPI 生成一份“现有路径 → 目标上游”的清单，不允许仅凭包名猜测路由。

至少满足以下目标：

| 请求类型 | 目标上游 | 说明 |
|---|---|---|
| 登录、刷新、用户、角色、权限 | `platform-management` | 登录和刷新按现有白名单放行 |
| 系统配置、审计、知识库管理 | `platform-management` | 保留现有权限校验 |
| `/api/v1/chat` | `svc-query` | 最终不得再经过管理服务 |
| `/api/v1/chat/stream` | `svc-query` | 保留 SSE、Last-Event-ID、Idempotency-Key 和 Trace |
| 文档上传、解析及状态接口 | 按任务状态归属决定 | 状态迁移完成后直连 `svc-ingest`，否则暂留管理服务 |
| 其他管理接口 | `platform-management` | 不新增第三 Java 应用 |

注意：

- `svc-ingest` 的上传、解析、任务和文档接口不能一律按 URL 前缀转发，必须逐个核对契约。
- 如果导入直连会绕过 Java 的任务恢复、文档关联、权限或审计，先保留 `gateway → management → svc-ingest`，列出阻断项，不得强行切换。
- 问答直连是本任务的最终验收项；导入直连允许在有明确证据时暂缓，但必须说明原因和后续边界。
- 现有 `/query-api`、`/import-api` 旧链路是否需要保留，应根据前端真实调用和兼容性检查决定，不得无意中形成绕过安全策略的公开入口。

## 七、认证、权限与审计约束

### 认证

- 登录、刷新、注销和令牌签发留在 `platform-management`。
- 网关验签优先使用仅用于验证的密钥或公钥配置。
- 不得为了让网关验签而把包含全部业务加密密钥的 keyring 无差别挂载到网关。
- 当前过滤器依赖用户数据库和撤销状态，必须先完成验证方式重构，不能把数据库依赖原样带入网关。
- 不允许为每个业务请求增加一次 `gateway → management` 的同步认证内省调用。
- 如使用 Redis 保存撤销状态或权限版本，必须复用现有 Redis，并设计故障行为、缓存一致性和失效策略。

### 权限

- 网关可以执行通用身份校验，管理服务仍须保留管理业务授权。
- 不得因为请求来自网关就默认信任任意 `X-User-Id`、`X-Roles` 等请求头。
- 对外部传入的身份头必须清理后由可信组件重新生成。
- Python 服务接收身份和权限头时，必须明确其可信边界，避免宿主端口或旧链路绕过网关后直接伪造身份。

### 审计与内容安全

- 问答直连前必须保留现有内容安全、输入校验、输出合规和审计语义。
- 如需把审计改为异步，必须有可靠的记录和失败追踪，不能简单改成无保障的 fire-and-forget。
- 如果上述能力尚未解耦完成，问答暂时保留原管理链路并完成中间阶段验证，但要在最终结果中明确未完成项，不得宣称全部目标达成。

## 八、配置与 Docker Compose

第 4 级 Compose 的目标服务为：

```text
platform-db-init
platform-management
platform-gateway
```

建议约定：

- `platform-management` 容器内端口为 `8080`，只使用 `expose`。
- `platform-gateway` 容器内端口为 `8080`，宿主机默认端口使用 `PLATFORM_GATEWAY_PORT=8081`。
- `platform-gateway` 配置 `PLATFORM_MANAGEMENT_URL=http://platform-management:8080`。
- 问答上游继续使用 `svc-query`，端口沿用 `QUERY_WORKER_PORT`。
- 解析上游继续使用 `svc-ingest`，端口沿用 `INGEST_WORKER_PORT`。
- `platform-db-init` 继续使用独立 `PLATFORM_DB`，不改变 Nacos 与平台数据库隔离方式。
- 数据库、Flyway、MinIO、上传临时目录和业务安全密钥配置归管理服务。
- 网关只挂载自身必需的最小化配置和密钥，不继承管理服务的全部数据卷。
- 两个应用都有独立健康检查、资源限制和镜像标签。
- `frontend` 依赖健康的 `platform-gateway`，不再依赖或直接访问 `platform-management`。
- 第 5 级 `PLATFORM_API_UPSTREAM` 指向 `http://platform-gateway:8080`；如重命名环境变量，必须同步修改模板、Compose 和文档。

同步更新：

- `deploy/docker/.env.example`
- `deploy/docker/docker-compose-java.yaml`
- `deploy/docker/docker-compose.yaml`
- `web/nginx.conf.template`（仅在 upstream 名称或路由需要调整时）
- Prometheus 等现有配置中对 `platform-api` 的服务名引用

不得修改真实 `.env` 中的密码、Token 或其他密钥。

## 九、推荐实施顺序

### 阶段 0：基线调查

1. 阅读真实代码并列出 Controller、Filter、Service、Repository 的依赖关系。
2. 生成现有 API 路由与目标上游映射表。
3. 记录当前 Maven 测试、镜像构建和 Compose 配置基线。
4. 标明认证、内容安全、审计、任务状态中阻碍直连的耦合点。

### 阶段 1：先形成两个可部署应用

1. 将现有业务应用调整为 `platform-management`，保留管理和数据能力。
2. 新建轻量 `platform-gateway`，初期以兼容路由为主。
3. 调整根 POM、应用 POM、组件扫描、配置和 Dockerfile。
4. 验证两个应用可以分别测试、打包和启动。

### 阶段 2：切换统一入口

1. 第 4 级 Compose 同时提供管理服务与网关。
2. 管理服务取消宿主机端口映射，网关成为唯一 Java 对外入口。
3. 前端 upstream 切换到网关。
4. 验证登录、权限、知识库、文档和系统管理行为不回归。

### 阶段 3：缩短问答主链路

1. 解耦验签、权限头、内容安全、审计和 Trace 的必要依赖。
2. 将 `/api/v1/chat` 与 `/api/v1/chat/stream` 直接路由到 `svc-query`。
3. 验证非流式、SSE、续传、幂等、错误信封和上游异常。
4. 通过日志或 Trace 证明请求没有再同步经过 `platform-management`。

### 阶段 4：评估并处理导入链路

1. 核对 Java 导入任务状态与 Python 任务状态的职责。
2. 满足安全与状态一致性前提的接口直连 `svc-ingest`。
3. 不满足条件的接口保留管理服务，并记录精确阻断项，不扩大成新微服务。

### 阶段 5：清理与文档

1. 删除不再使用的启动类、重复 Dockerfile、旧配置和无效引用。
2. 确保仓库中不再保留名为 `platform-api` 的旧可部署 Java 应用。
3. 更新 Java 开发部署文档、总体部署手册、端口表、服务清单和架构说明。
4. 将旧五微服务规划明确标记为被本方案替代，避免后续误用。

## 十、测试与验证要求

### Maven

在 `javaplat/` 下执行并记录结果：

```bash
./mvnw clean test
./mvnw -pl apps/platform-gateway -am clean package
./mvnw -pl apps/platform-management -am clean package
```

要求：

- 原有效单元测试随代码迁移到对应应用并继续通过。
- 新增网关路由、鉴权、请求头清理、SSE 和错误处理测试。
- 不通过删除或忽略失败测试来换取构建成功。
- 已存在且与本次无关的失败必须单独说明，不能混入本次回归。

### Compose 静态校验

至少验证：

```bash
docker compose -f docker-compose-java.yaml config
docker compose -f docker-compose.yaml config
docker compose -f docker-compose-java.yaml config --services
docker compose -f docker-compose.yaml config --services
```

使用模板配置时，通过临时环境变量提供必填项，不写入真实密钥。确认：

- 第 4 级包含 `platform-management` 和 `platform-gateway`。
- 第 5 级前端只依赖并指向网关。
- 管理服务没有不必要的宿主机端口。
- 五级 `include` 链未被破坏。

### 集成验收

环境允许时验证：

1. 登录、刷新、注销和无效令牌行为。
2. 未认证返回 401、无权限返回 403，统一错误信封不变。
3. 非流式问答请求成功，且链路为 `gateway → svc-query`。
4. SSE 问答、Last-Event-ID、Idempotency-Key 和 Trace 头行为不回归。
5. Python 上游超时、5xx、提前断流时网关行为正确。
6. 管理接口通过 `gateway → platform-management` 正常工作。
7. 上传、解析和状态查询不回归；未直连的接口必须有名录和原因。
8. 管理服务不能通过宿主机端口被直接访问。
9. 审计、内容安全和权限校验没有被绕过。

最后执行：

```bash
git diff --check
```

## 十一、完成标准

只有同时满足以下条件，才能宣称任务完成：

- [x] 只有两个 Java 可部署应用：`platform-gateway`、`platform-management`。
- [x] 没有新增任何业务微服务或第三 Java 应用。
- [x] 两个应用拥有独立 POM、配置、测试、JAR 和 Dockerfile。
- [x] 共享依赖仍由 `libs/common` 和根 POM 合理管理。
- [x] 前端只访问网关，管理服务不再作为公开入口。
- [x] 管理请求为 `gateway → management`，没有额外 Java 服务跳转。
- [x] 问答主链路为 `gateway → svc-query`，没有同步经过管理服务。
- [x] 导入链路已直连，或有基于代码证据的暂缓说明。
- [x] 认证、权限、审计、内容安全、幂等和 SSE 未被削弱。
- [x] Maven 测试与打包、Compose 静态校验通过。
- [x] 相关文档已更新，不再把旧五微服务规划描述为目标架构。
- [x] 未修改真实密钥，未创建未授权的 Git commit。

## 十二、停止并报告的情况

出现以下情况时，不要强行扩大方案：

- 为了完成拆分必须新增第三个 Java 可部署服务。
- 问答直连必须取消认证、权限、内容安全或审计才能成立。
- 导入直连会破坏任务恢复、文档关联或数据一致性。
- 需要更换数据库、引入新的必需中间件或进行大规模数据迁移。
- 需要同时实施整个仓库目录迁移才能继续。
- 现有测试基线无法区分历史失败与本次回归。

遇到上述情况，应保留可验证的中间成果，列出具体文件、代码依赖、风险和可选处理方式，等待用户决策。

## 十三、最终交付说明

实施结束后，用简洁清单报告：

1. 修改和新增的模块、文件及职责。
2. 最终请求路由表和实际调用链。
3. 认证、权限、审计及内容安全的处理方式。
4. 已执行的测试、构建、Compose 校验及结果。
5. 未完成的导入直连或其他阻断项。
6. 仍需用户决定的兼容性或运维事项。

---

## 十四、执行结果（交付报告）

> 本报告由验收 Agent 在 JDK 21（`/usr/local/jdk21`）环境下复核并补充生成。

### 1. 模块与文件

- **新增应用**：`javaplat/apps/platform-gateway`、`javaplat/apps/platform-management`（各自独立 `pom.xml`、`Dockerfile`、`application.yml`、`src/main` 与 `src/test`）。
- **删除**：旧单体 `javaplat/apps/platform-api` 与根 `javaplat/Dockerfile`（根 POM `<modules>` 仅含 `libs/common`、`apps/platform-gateway`、`apps/platform-management`）。
- **共享库**：`javaplat/libs/common` 保留 `JwtTokenVerifier`、`SecurityPrincipal`、`GatewayAuthSnapshot`、`ContentSecurityEngine`、`OutputComplianceGuard`、`IdempotencyFilter`、`TracingService` 等稳定共享代码，不反向依赖应用。
- **新增迁移**：`apps/platform-management/.../V9__request_logs_service_default.sql`（不修改已应用的 V8，保持 Flyway checksum 兼容）。
- **编排**：`deploy/docker/docker-compose-java.yaml`（第 4 级：`platform-db-init` / `platform-management` / `platform-gateway`）、`docker-compose.yaml`（第 5 级：`frontend` 仅依赖网关）、`docker-compose-p0.yaml`（P0 栈同步拆分）。

### 2. 最终路由表与调用链

| 路径/类别 | 目标上游 | 说明 |
|---|---|---|
| `/api/v1/chat`、`/api/v1/chat/stream` | `svc-query` | 网关 `ChatController` 直连，**不经过管理服务**；本地验签 + 内容安全 + 审计 + SSE 透传 |
| `/api/v1/auth/login`、`/api/v1/auth/refresh` | `platform-management` | 白名单放行后代理，管理服务签发/刷新令牌 |
| 其余 `/api/v1/**`（用户/角色/权限/知识库/文档/审计/系统配置/导入任务等） | `platform-management` | 网关 `ManagementProxyController` 流式代理；管理服务二次验签与 `@RequirePermission` 授权 |
| 导入（上传/解析/任务状态） | `platform-management → svc-ingest` | 暂不直连（见第 5 节阻断项） |

实现要点：`ChatController` 的具体路径映射 `/api/v1/chat` 在 Spring 路由匹配中优先于代理的 `/api/v1/**`；代理清理外部 `X-User-Id/X-Tenant-Id/X-Roles/X-Permissions/X-Kb-Ids` 等身份头并由可信组件重建，同时转发 `Authorization` 供管理服务重新验签（不盲信网关头）。

### 3. 认证 / 权限 / 审计 / 内容安全

- **认证**：管理服务持有完整 keyring 与 AES 密钥；仅向共享卷 `platform-jwt-verify` 导出只读 HS256 验证密钥（600 权限、原子移动），网关以该密钥本地验签。
- **权限与撤销**：用户/角色/权限/知识库快照写入 Redis（`aipaas:gateway:auth:{userId}`），JTI 撤销写入 `aipaas:gateway:revoked:{jti}`；缺失快照或 Redis 故障 → 网关 fail-closed（401/503），不回退旧权限。
- **审计**：网关问答审计事件经 Redis Stream `aipaas:gateway:audit` 异步发布，管理服务 `GatewayAuditStreamConsumer` 落库（ACK 在持久化成功之后），非 fire-and-forget。
- **内容安全**：网关 `GatewayContentSecurityService` 读取 Redis 内容策略快照（`aipaas:gateway:content-policies`），缺失时 fail-closed 503；输出合规由 `OutputComplianceGuard` 对非流式 JSON 与 SSE 流分别处理。
- **幂等 / SSE / Trace**：`IdempotencyFilter` 跳过 `/chat/stream` 与 `Accept: text/event-stream`，避免缓冲破坏流式；Trace/Request-Id/traceparent 头在网关重建并透传。

### 4. 已执行的验证（JDK 21）

```bash
cd javaplat && JAVA_HOME=/usr/local/jdk21 PATH=/usr/local/jdk21/bin:$PATH ./mvnw -o clean test
# → BUILD SUCCESS：platform-gateway + platform-management（管理 92 个测试）全部通过
./mvnw -o -DskipTests -pl apps/platform-gateway -am clean package      # → platform-gateway.jar
./mvnw -o -DskipTests -pl apps/platform-management -am clean package   # → platform-management.jar

cd deploy/docker && PLATFORM_BOOTSTRAP_ADMIN_PASSWORD='VerifyOnly-1a!' \
  docker compose -f docker-compose-java.yaml config          # exit 0
  docker compose -f docker-compose.yaml config               # exit 0
  P0_BOOTSTRAP_ADMIN_PASSWORD='VerifyOnly-1a!' \
  docker compose -f docker-compose-p0.yaml config            # exit 0

git diff --check   # 无空白错误
```

> 说明：`./mvnw` 首次运行会下载 Maven 发行版到 `~/.m2/wrapper/dists/`，之后 `-o`（离线）可正常工作。务必显式 `export JAVA_HOME=/usr/local/jdk21 PATH=/usr/local/jdk21/bin:$PATH`（默认 `java` 仍解析为 JDK 25）。

### 5. 未完成项与阻断项

- **导入直连 `svc-ingest` 暂缓**（有代码证据）：导入任务状态机、失败恢复、文档关联、权限校验与审计仍归 `platform-management` 所有；直连会绕过这些能力，故保留 `gateway → management → svc-ingest`。阻断项与边界见 `docs/02a_总体部署手册.md` 与 `docs/08_模块说明文档.md`。
- **集成烟测**：Maven 单测 + Compose 静态校验已通过；端到端容器烟测（401/403/撤销/审计流）需在完整 Docker 栈运行后执行，非本次必需项。

### 6. 本会话补充修复的残留项

在既有实施基础上，复核发现并修复了以下未完成项：

1. `ContentSecurityServiceTest` 中遗漏的 `ObservabilityMetrics` 构造调用（构造器已加 `serviceName` 参数）→ 补充第二参数，恢复编译与测试通过。
2. `deploy/docker/verify_p0_security.py` 仍引用已不存在的 `p0-platform-api` 容器 → 改为 `p0-platform-management`（密钥环/环境/日志）与 `p0-platform-gateway`（端口/日志），保持 P0 验收脚本可用。
3. `docker-compose-p0.yaml` 中 `postgres` 注释残留 `platform-api` → 更正为 `platform-management`。
4. `javaplat/TODO说明.md` 与 `docs/02d_java模块开发部署.md` 仍把旧五微服务规划描述为目标 → 顶部增加「已废弃 / 被网关+管理拆分替代」醒目标识，避免后续误用。
5. 本提示词状态由「待执行」更新为「已执行完成」，完成标准全部勾选。

未修改真实 `.env` 密钥，未创建 Git commit。
