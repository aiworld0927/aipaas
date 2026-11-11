# 配置管理改造任务清单（对应 spec.md）

## T1 YAML 配置包
- [x] 新建 pymodules/configs/aipaas_config.base.yaml（spec §1 全量值）
- [x] 新建 pymodules/configs/aipaas_config.devcompany.yaml
- [x] 新建 pymodules/configs/aipaas_config.devhome.yaml（同构替换主机，标注运行时验证）
- [x] 实测 OmegaConf 嵌套默认值 `${oc.env:A,${oc.env:B,null}}`；实测通过（omegaconf 2.4.0），采 YAML 嵌套写法

## T2 加载器与 schema
- [x] packages/common/pyproject.toml + pydantic>=2，uv lock
- [x] conf/schemas/common.py（CommonSettings 及 9 个子模型，密钥 Optional[SecretStr]，extra=forbid）
- [x] conf/schemas/{ingest_worker,query_worker,model_embedding_worker,model_reranker_worker}.py
- [x] conf/schemas/__init__.py 注册表
- [x] conf/settings.py：load_settings / get_settings / SettingsError（spec §2 十步流程）
- [x] conf/__init__.py re-export
- [x] packages/common/tests/test_settings.py 10 用例全绿（含真实配置包 happy path）

## T3 P0 packages 切换
- [x] core/log.py（apply_log_config + 控制台兜底）
- [x] utils/path_util.py（去 load_dotenv 副作用）
- [x] minio_utils.py / mongo_history_utils.py（纯惰性）/ milvus_utils.py
- [x] lm_utils.py / llm_client.py / embedding_http_utils.py / reranker_http_utils.py
- [x] mcp-agents1.0/mcp_client.py（URL 配置化 + bailian_api_key bug 修复）

## T4 P1 死代码删除（每个先 grep 复核）
- [x] conf/app_config.py、mongo_client.py、mysql_client_manager.py
- [x] retrieval/{qdrant,es,neo4j}_client_manager.py、llm/embedding_client_manager.py
- [x] pymodules/configs/aipaas_config.yaml、pymodules/.env-参考
- [x] 旧 conf 五模块（minio/mineru/milvus/lm/bailian_mcp_config）随 T5 完成后删除

## T5 P2 模块改造
- [x] ingest：bootstrap（api/service.py + graph/main.py）+ 5 节点 + 删 config.py（c_node_md_img 修 secure scheme）
- [x] query：bootstrap×3（service/main_v1/main_v2）+ 5 节点 + 删 config.py
- [x] model-embedding：bootstrap + 3 文件 + Semaphore 惰性 + 删 config.py
- [x] model-reranker：同上

## T6 P3 部署与 env
- [x] Dockerfile ×4（COPY configs、去 LOG_* ENV）
- [x] docker-compose.yaml（APP_ENV/CONFIG_DIR/卷映射/精简 environment）
- [x] deploy/docker/.env.example、pymodules/.env 精简、新建 pymodules/.env.example
- [x] .gitignore / pymodules/.dockerignore 核查

## T7 验证
- [x] unittest 全绿（10/10 OK）
- [x] 四模块 load_settings 冒烟（devcompany + dummy 密钥，四进程全 OK）
- [x] compileall + ingest/query api.service import 冒烟（uv sync --all-packages --group cpu 后全通过）
