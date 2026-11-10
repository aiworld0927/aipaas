# javaplat Java 平台模块规划（TODO）

> **状态：规划中，暂不打包部署。** 本目录暂只做模块规划与功能拆解，待评审通过后再补充源码、pom.xml 与 Dockerfile，并纳入 `deploy/docker/docker-compose.yaml` 编排。
>
> 上次会话预创建的 5 个 Dockerfile 已删除，避免与最终方案冲突。

## 1. 模块总览

| 模块 | 容器内端口 | 职责 | 依赖 |
| --- | --- | --- | --- |
| gateway | 8080 | 统一入口网关 | nacos、redis |
| svc-auth | 8081 | 认证与权限 | nacos、postgres、redis |
| svc-document | 8082 | 文档管理调度 | nacos、postgres、redis、minio、rocketmq、svc-ingest |
| svc-search | 8083 | 检索编排 | nacos、postgres、redis、svc-query |
| svc-agent | 8084 | 智能体编排 | nacos、postgres、redis、svc-query |

技术栈约定：JDK 21、Spring Boot 3.x、Spring Cloud Gateway、Nacos（注册+配置中心）、PostgreSQL、Redis、RocketMQ、MinIO。

## 2. 各模块功能拆解（待评审）

### 2.1 gateway（网关服务）

- [ ] 统一路由转发：按服务名路由到 svc-auth / svc-document / svc-search / svc-agent
- [ ] JWT 鉴权过滤器：校验 Token，白名单放行（登录、健康检查）
- [ ] 跨域（CORS）统一配置
- [ ] 请求日志与链路 ID（traceId）注入
- [ ] 限流（基于 Redis，接口维度）
- [ ] 服务熔断降级（对接 Nacos 服务发现）
- [ ] 对外端口映射：宿主机 8085 → 容器 8080

### 2.2 svc-auth（认证服务）

- [ ] 用户管理：用户增删改查、密码加密存储
- [ ] 角色与权限：RBAC 模型（角色 → 权限点 → 菜单/接口）
- [ ] 登录认证：账号密码登录，签发 JWT；Token 刷新与注销
- [ ] 接口鉴权：网关转发后二次校验操作权限
- [ ] 组织架构：部门树维护
- [ ] 操作审计日志记录
- [ ] 对外端口：宿主机 8081 → 容器 8081

### 2.3 svc-document（文档服务）

- [ ] 文档元数据管理：上传记录、状态（解析中/完成/失败）、版本
- [ ] 文件上传调度：接收文件 → 存 MinIO → 发 RocketMQ 消息 → 调用 Python svc-ingest 解析
- [ ] 解析状态跟踪：消费 svc-ingest 回调/消息，更新状态并通知前端
- [ ] 知识库管理：知识库（集合）维度组织文档，关联 Milvus Collection
- [ ] 文档删除：同步清理 MinIO 对象与 Milvus 向量数据
- [ ] 对外端口：宿主机 9082 → 容器 8082

### 2.4 svc-search（检索服务）

- [ ] 检索编排：接收查询 → 调用 Python svc-query → 结果聚合返回
- [ ] 检索历史：记录会话与问答历史（MongoDB/PostgreSQL）
- [ ] 结果后处理：引用溯源、高亮标注
- [ ] 检索统计：查询量、响应耗时、命中率指标上报
- [ ] 对外端口：宿主机 8083 → 容器 8083

### 2.5 svc-agent（智能体服务）

- [ ] 智能体配置管理：Agent 定义（提示词、工具、模型参数）CRUD
- [ ] 会话编排：多轮对话管理，串联 svc-search 检索能力
- [ ] 工具调用：MCP 工具接入与调度
- [ ] 流式输出：SSE 推送模型响应
- [ ] 对外端口：宿主机 8086 → 容器 8084

## 3. 后续落地步骤（评审通过后执行）

1. 搭建 Maven 多模块父 pom（javaplat/pom.xml），声明公共依赖与版本
2. 按模块补充源码（每个模块一个子目录：`<module>/pom.xml` + `src/`）
3. 每个模块补 Dockerfile（统一模板：`eclipse-temurin:21-jre-alpine` 运行 jar，`VOLUME /app/logs`，日志文件名用 `LOG_FILE_PREFIX=<模块名>`，健康检查 `/actuator/health`）
4. `deploy/docker/docker-compose.yaml` 增加对应服务条目（build context `../../javaplat`，depends_on nacos/postgres/redis 均 `service_healthy`）
5. `deploy/docker/.env.example` 取消注释各服务端口变量
6. 更新 `docs/02d_java模块开发部署.md` 为实际部署文档

## 4. 评审确认点

- [ ] 各模块端口分配是否合理（8080–8084，网关对外 8085）
- [ ] svc-document 是否直调 svc-ingest，还是全部走 RocketMQ 异步
- [ ] 检索历史存储选型：PostgreSQL 还是 MongoDB
- [ ] 是否需要独立的用户中心前端，还是复用 web 模块
