# 配置管理改造验收清单（对应 spec.md §4）

## 部署与 env（T6）
- [x] 4 个 Dockerfile 均无 LOG_* ENV，embedding/reranker 含 `COPY configs ./configs`
- [x] docker-compose.yaml 四 Python 服务 environment 仅 APP_ENV/CONFIG_DIR/密钥/TZ，含 `${CONFIG_PACKAGE_DIR:-../../pymodules/configs}:/app/configs:ro` 卷
- [x] deploy/docker/.env.example 含 APP_ENV/CONFIG_DIR/MINERU_API_TOKEN，已迁移变量已删除
- [x] pymodules/.env 精简为 APP_ENV/CONFIG_DIR/密钥；pymodules/.env.example 已新建
- [x] .gitignore / pymodules/.dockerignore 排除 .env.*（保留 .env.example）

## 运行验证（T7）
- [x] packages/common/tests/test_settings.py unittest 全绿（10/10 OK）
- [x] 四模块 load_settings 冒烟通过（真实 pymodules/.env，APP_ENV=devcompany）
- [x] uv sync --all-packages --group cpu 成功
- [x] python -m compileall 全部改动包无语法错误
- [x] ingest_worker.api.service / query_worker.api.service import 冒烟通过
