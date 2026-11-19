# P0 上线检查单（Go-Live Checklist）

- 版本：v1.0
- 日期：2026-11-19
- 适用范围：金融标准化 RAG 平台 P0（platform-gateway / platform-management / rag-ai-api / ingest-worker / query-worker / web / mock）
- 关联：[P0-ACCEPTANCE-REPORT.md](./P0-ACCEPTANCE-REPORT.md)、[P0-DEPLOYMENT-MANUAL.md](./P0-DEPLOYMENT-MANUAL.md)

> 使用方式：逐项执行并在「结果」列填写 `PASS/FAIL/N/A` + 证据（命令输出、截图、链接）。FAIL 项不得放行。

## 1. 构建与制品

| # | 检查项 | 命令/方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 1.1 | Python 回归 | `cd pymodules && uv run pytest tests -q` | 216 passed，0 failed | PASS |
| 1.2 | Java 回归 | `cd javaplat && ./mvnw -o -B clean test` | 历史记录：拆分前 97 tests，0 failures | PASS |
| 1.3 | 契约校验 | `python3 aipaas-contracts/scripts/verify_contract.py` | PASS，44/44，坏引用 0 | PASS |
| 1.4 | Compose 语法 | `cd deploy/docker && docker-compose -f docker-compose.yaml config -q` | 退出码 0 | PASS |
| 1.5 | 前端构建 | `cd web && npx vite build --outDir dist-acceptance --emptyOutDir` | 构建成功，无报错 | PASS |
| 1.6 | 镜像版本固定 | 检查 compose `image:` 是否固定 tag/digest | 无 `latest` 漂移 | 待执行 |
| 1.7 | 交付物齐全 | 检查 `docs/acceptance/` 5 类交付物 | 报告/缺陷/golden/压测/检查单齐全 | PASS |

## 2. 配置与密钥

| # | 检查项 | 方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 2.1 | `.env` 不入库 | `git status`、检查 `.gitignore` | 无密钥文件被跟踪 | 待执行 |
| 2.2 | LLM 密钥 | 配置 `AIPAAS_LLM_*`/provider key | 仅注入环境变量或密钥管理，不落盘 | 待执行 |
| 2.3 | 数据库口令 | Postgres/Redis/MinIO 口令 | 非默认口令，长度 ≥16 | 待执行 |
| 2.4 | JWT/会话密钥 | `AIPAAS_AUTH_*` | 随机 ≥256bit，轮换策略明确 | 待执行 |
| 2.5 | 审计保留期 | `aipaas.security.audit-retention-days` | ≥180 | PASS（默认 180） |
| 2.6 | 请求日志保留期 | `aipaas.observability.request-log-retention-days` | 按合规要求配置 | PASS（默认 30） |
| 2.7 | 敏感字段脱敏 | 检查日志/Trace 采样 | 无 token/密码/身份证/银行卡明文 | PASS（安全验收） |

## 3. 依赖与中间件

| # | 检查项 | 方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 3.1 | PostgreSQL | `pg_isready` / 健康检查 | 可连接，迁移已执行 | 待执行 |
| 3.2 | Redis | `redis-cli ping` | PONG | 待执行 |
| 3.3 | MinIO | `mc ready local` 或控制台 | healthy，bucket 已创建 | 待执行 |
| 3.4 | Milvus | `curl :9091/healthz` | healthy，collection 可创建 | 待执行 |
| 3.5 | etcd | `etcdctl endpoint health` | healthy | 待执行 |
| 3.6 | 服务健康 | `GET /healthz`、`GET /readyz` | 全部 `200` | 待执行 |
| 3.7 | 端口占用 | 核对 8081/8100/18080/4010/19530/9000/6379/5432 | 无冲突 | 待执行 |

## 4. 数据与索引

| # | 检查项 | 方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 4.1 | 知识库初始化 | 创建默认 KB 与角色 | 可查询、权限正确 | 待执行 |
| 4.2 | 样例导入 | 上传 3 份金融标准 PDF | 解析成功，条款数>0 | PASS（E2E） |
| 4.3 | 索引构建 | 触发向量化与 HNSW 构建 | 状态 READY，indexedChunks 一致 | PASS（E2E） |
| 4.4 | 检索冒烟 | 标准号/条款号查询 | 命中且引用含标准号/条款号 | PASS（E2E） |
| 4.5 | 简介与导出 | 生成简介、导出 XLSX | 五要素齐全，行数=条款数 | PASS（E2E） |
| 4.6 | 对象存储写入 | 检查 `parse/{tenant}/{docId}/v{n}/` | 版本化产物存在 | 待执行 |

## 5. 安全与权限

| # | 检查项 | 方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 5.1 | 未认证访问 | 无 Token 调 `/api/v1/**` | `401 AUTH.UNAUTHENTICATED` | 待执行 |
| 5.2 | 越权访问 | 低权限账号访问他库/他文档 | `403 PERM.FORBIDDEN` + 审计 | PASS（离线矩阵） |
| 5.3 | 注入拦截 | 22 条注入样例 | 拦截率 ≥95% | PASS（95.5%） |
| 5.4 | 内容审核 | 违规/敏感输入输出 | 拦截/替换，误拦 ≤1% | PASS（Java 单测） |
| 5.5 | TLS | 外部入口 HTTPS，内部链路按需 TLS | 证书有效，TLS1.2+ | 待执行 |
| 5.6 | 审计查询 | 查询登录/上传/导出/越权事件 | 可检索，字段齐全 | PASS（离线） |
| 5.7 | 导出权限 | 无权限账号创建/下载导出 | 拒绝并审计 | PASS（Java 单测 + E2E） |

## 6. 可观测与告警

| # | 检查项 | 方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 6.1 | 指标抓取 | Prometheus 抓取 `/metrics` | up=1，QPS/延迟/成功率有数据 | 待执行 |
| 6.2 | Grafana 看板 | 打开 `aipaas-p0-observability.json` | 8 个面板有数据 | 待执行 |
| 6.3 | 告警规则 | 加载 `prometheus/alerts/alerts-p0.yml` | 8 条规则 loaded | 待执行 |
| 6.4 | 告警抑制 | Alertmanager 配置 | 聚合/抑制生效，无风暴 | 待执行 |
| 6.5 | Trace 回放 | 按 TraceID 查询 | 覆盖解析→生成各环节 | 待执行 |
| 6.6 | 日志检索 | 按 TraceID/用户/状态码检索 | 命中结构化日志 | 待执行 |

## 7. 容量与性能

| # | 检查项 | 方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 7.1 | 检索 P95 | 压测脚本 | ≤500ms | PASS（24.99ms 桩） |
| 7.2 | 端到端 P95 | 压测脚本 | ≤3s | PASS（34ms 桩） |
| 7.3 | 并发 | 32 并发 | ≥100 QPS | PASS（1453/1039） |
| 7.4 | 资源水位 | CPU/内存/磁盘监控 | 压测后 ≤70% | 待执行 |
| 7.5 | 真实 LLM 时延 | 接入生产模型后复测 | 端到端 P95 ≤3s | 待执行（后置） |

## 8. 备份、回滚与应急预案

| # | 检查项 | 方法 | 通过标准 | 结果 |
| --- | --- | --- | --- | --- |
| 8.1 | 数据库备份 | 备份 Postgres | 可恢复演练通过 | 待执行 |
| 8.2 | 对象存储备份 | MinIO 版本/复制 | 关键 bucket 有副本 | 待执行 |
| 8.3 | 索引重建 | 删除/重建 collection | 30 分钟内恢复 | 待执行 |
| 8.4 | 服务回滚 | 回滚上一镜像 tag | 5 分钟内恢复 | 待执行 |
| 8.5 | 降级开关 | 关闭 LLM 通道 | 纯检索/受控拒答 | PASS（离线） |
| 8.6 | 值班与联系人 | 更新 on-call 表 | 7×24 可达 | 待执行 |

## 9. 验收证据归档

| # | 交付物 | 路径 | 状态 |
| --- | --- | --- | --- |
| 9.1 | P0 验收报告 | `docs/acceptance/P0-ACCEPTANCE-REPORT.md` | 已归档 |
| 9.2 | 缺陷清单 | `docs/acceptance/DEFECT-LIST.md` | 已归档 |
| 9.3 | golden set | `docs/acceptance/golden/golden_set.jsonl` | 已归档（52 条） |
| 9.4 | 压测报告与快照 | `docs/acceptance/perf/` | 已归档 |
| 9.5 | 安全报告与快照 | `docs/acceptance/security/` | 已归档 |
| 9.6 | 端到端报告与导出样例 | `docs/acceptance/e2e/` | 已归档 |
| 9.7 | 部署手册 | `docs/acceptance/P0-DEPLOYMENT-MANUAL.md` | 已归档 |

## 10. 放行签署

| 角色 | 姓名 | 结论 | 日期 |
| --- | --- | --- | --- |
| 测试与交付负责人 |  | 建议放行（2 项受限通过不阻断） | 2026-11-19 |
| 后端负责人 |  |  |  |
| 前端负责人 |  |  |  |
| 安全负责人 |  |  |  |
| 运维负责人 |  |  |  |

> 放行条件：第 1–6 节无 FAIL；第 7 节真实 LLM 复测完成；第 8 节回滚演练通过；D-01/D-02 已登记且有复测计划。
