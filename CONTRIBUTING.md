# 贡献指南

感谢参与 AIPaaS。提交代码前，请先阅读[行为准则](CODE_OF_CONDUCT.md)、[架构说明](docs/03_架构说明.md)和[快速使用入门](docs/00_快速使用入门.md)。

## 1. 开始之前

- 新功能、契约变更和跨模块改造先创建 Issue，说明问题、范围、验收标准和分期。
- 修复较小文档或缺陷可直接提交 Pull Request，但仍需完整描述复现与验证。
- 不将 P1/P2 能力悄悄加入 P0；混合检索、重排、多轮会话、OCR、统一网关等按产品规划评审。
- 安全漏洞不要提交公开 Issue 或 PR，按[安全政策](SECURITY.md)私密报告。

## 2. 开发环境

| 范围 | 要求 |
| --- | --- |
| Java | JDK 21；使用仓库 `javaplat/mvnw`，不要求全局 Maven |
| Python | Python 3.12、uv；统一使用 `pymodules/.venv` |
| Web | Node.js 20、npm；依赖锁定在 `web/package-lock.json` |
| 部署 | Docker 24+、Docker Compose v2；两套 Compose 按场景选择 |
| 契约 | Python 3 与契约校验脚本所需依赖 |

首次使用按[快速入门](docs/00_快速使用入门.md)选择 Mock、P0 Compose、离线验收或开发全量环境，不要混用端口和上游配置。

## 3. 分支与提交

从最新默认分支创建短生命周期分支：

```text
feat/<scope>-<summary>     新功能
fix/<scope>-<summary>      缺陷修复
docs/<scope>-<summary>     文档
refactor/<scope>-<summary> 不改变行为的重构
test/<scope>-<summary>     测试与验收
chore/<scope>-<summary>    构建或工程维护
```

提交信息推荐 Conventional Commits：

```text
feat(retrieval): 支持标准编号归一化
fix(export): 下载前重新校验知识库权限
docs(quickstart): 澄清 P0 容器集成边界
```

每个提交应保持可构建、职责单一；不要夹带无关格式化、生成产物或大文件。

## 4. 各模块约定

### 4.1 契约优先

1. 跨端 API 变更先修改 `aipaas-contracts/openapi/aipaas-openapi-v1.yaml`，不要在 Java、Python、Web 各维护一份定义。
2. 对齐 `x-p0-feature`、`x-owner`、`x-permission`、`x-audit`、`x-idempotency` 和预留字段声明。
3. 运行 `python3 aipaas-contracts/scripts/verify_contract.py`，并执行 Mock 冒烟。
4. 破坏性变更遵循[版本兼容策略](aipaas-contracts/VERSIONING.md)，提前告警，不直接删除字段或枚举。
5. 契约实现状态必须在文档中说明；骨架、Mock、Stub 和真实实现不得混称。

### 4.2 Java

- 仅修改 `javaplat` 时，运行 `./mvnw -pl apps/platform-api -am test`。
- 所有写接口必须声明权限点与审计点；上传、删除、导出等还要校验对象归属。
- 统一使用 `{code,message,data,traceId}` 信封和分段错误码。
- 下游调用必须有超时、受控异常和 Trace 传播；不得吞掉传输失败。
- 密钥、口令、完整敏感 Prompt 不得进入日志、异常或响应。

### 4.3 Python

- 在 `pymodules` 根目录使用 `uv sync --all-packages --group cpu` 或 `gpu`，两组互斥。
- 依赖方向保持 `apps/* → packages/*`，共享包不得反向依赖应用。
- 服务入口使用 `python -m <module>.api.app`，先加载配置再导入业务模块。
- 解析原文、清洗产物和版本化结果不得互相覆盖；重试不得制造重复 Chunk。
- 检索必须在证据进入生成前完成权限裁剪；无证据时拒答，不通过 Prompt 伪装权限控制。
- 离线替身必须命名清楚，性能和质量报告必须注明 Stub/真实模型环境。

### 4.4 Web

- 使用 `web/src/api/p0Client.js` 和统一 API Base，不在页面中散落硬编码服务地址。
- 引用、拒答、任务失败、权限错误和 SSE 错误应有可见状态。
- 演示登录、Mock 数据和真实 Bearer 认证必须明确区分。
- 提交前至少执行 `npm run build`；涉及交互时补充实际验证步骤。

### 4.5 部署与文档

- P0 Compose 与开发全量 Compose 分别验证，不复制出一套冲突的端口和启动命令。
- 修改端口、环境变量、权限、存储或下游调用时，同步更新架构、快速入门和部署手册。
- 文档中的命令、相对链接和证据路径应可执行或可访问。
- 不提交 `.env`、真实模型、数据卷、用户文档、导出文件、日志或临时构建产物。

## 5. 验证要求

按变更影响选择最小但充分的验证，PR 中填写实际命令和结果：

| 变更范围 | 必做验证 |
| --- | --- |
| OpenAPI | `python3 aipaas-contracts/scripts/verify_contract.py` + Mock 上传→问答冒烟 |
| Java | `cd javaplat && ./mvnw -pl apps/platform-api -am test` |
| Python | `cd pymodules && uv run pytest tests -q` |
| Web | `cd web && npm ci && npm run build` |
| Compose | 在 `deploy/docker` 执行对应 `docker compose ... config -q`，说明实际启动/健康检查结果 |
| 检索/生成/分块 | 额外运行 `e2e_p0.py`、`golden_eval.py`；性能或降级变更再跑 `load_test.py` |
| 权限/注入/审计/导出 | 额外运行 `security_checks.py` 和对应 Java 安全测试 |
| 文档 | 校验相对链接、命令、端口、路径及“当前实现/规划”表述 |

测试未执行时必须写明原因和剩余风险，不得把未运行写成通过。

## 6. Pull Request 流程

1. 同步目标分支，解决冲突并自查 diff。
2. 使用[PR 模板](.github/PULL_REQUEST_TEMPLATE.md)说明问题、方案、影响范围和证据。
3. 关联 Issue；契约、权限、审计、部署或文档有变化时明确勾选。
4. 保持讨论聚焦，评审意见修复后追加提交，不覆盖他人工作。
5. 合并前确认没有敏感信息、临时调试代码、无关文件和未说明的测试缺口。

## 7. 完成定义

一个变更只有同时满足以下条件才可视为完成：

- 行为符合对应验收标准，且没有越界实现后续阶段能力；
- 权限、审计、拒答、失败和降级路径与主路径一起考虑；
- 自动化测试或可复现证据覆盖变更；
- 文档和配置与实际代码一致；
- PR 中的结论真实、可复现、无敏感信息。
