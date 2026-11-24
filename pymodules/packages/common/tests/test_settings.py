"""load_settings 单元测试。

happy path 使用仓库真实配置包（pymodules/configs）；
失败/边界场景使用 tmp 目录中的最小配置包，保证用例间隔离。
"""
import os
import tempfile
import unittest
from pathlib import Path

from omegaconf.errors import InterpolationResolutionError
from pydantic import ValidationError

from aipaas_common.conf import settings as settings_mod
from aipaas_common.conf.settings import SettingsError, get_settings, load_settings

REPO_CONFIG_DIR = Path(__file__).absolute().parents[3] / "configs"

# 受测试影响的环境变量，setUp/tearDown 中快照恢复
_ENV_KEYS = [
    "CONFIG_DIR", "APP_ENV",
    "OPENAI_API_KEY", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
    "MONGO_URL", "MINERU_API_TOKEN",
    "EMBEDDING_ONLINE_API_KEY", "TEXT_RERANK_API_KEY",
    "NACOS_ENABLED", "NACOS_SERVER_ADDR", "NACOS_NAMESPACE",
    "NACOS_GROUP", "NACOS_USERNAME", "NACOS_PASSWORD", "NACOS_REGISTER_IP",
]

MINIMAL_COMMON = """
common:
  llm:
    base_url: http://llm.local/v1
    api_key: ${oc.env:OPENAI_API_KEY,null}
    llm_model: m1
    llm_temperature: 0.1
    vl_model: v1
  milvus: {url: http://milvus.local:19530, chunks_collection: c, item_name_collection: i, entity_name_collection: e}
  minio:
    endpoint: minio.local:9000
    secure: false
    bucket_name: b
    img_dir: img
    access_key: ${oc.env:MINIO_ACCESS_KEY,null}
    secret_key: ${oc.env:MINIO_SECRET_KEY,null}
  mongo: {url: "${oc.env:MONGO_URL,null}", db_name: d}
  mineru: {base_url: http://mineru.local, api_token: "${oc.env:MINERU_API_TOKEN,null}"}
  mcp: {mcp_base_url: http://mcp.local, api_key: "${oc.env:OPENAI_API_KEY,null}"}
  nacos:
    enabled: false
    server_address: nacos.local:8848
    namespace: public
    group_name: DEFAULT_GROUP
    username: "${oc.env:NACOS_USERNAME,null}"
    password: "${oc.env:NACOS_PASSWORD,null}"
    cluster_name: DEFAULT
    weight: 1.0
    register_ip: null
    log_level: INFO
    grpc_timeout_ms: 5000
  embedding_service: {url: http://e.local:8101, timeout: 120}
  rerank_service: {url: http://r.local:8102, timeout: 60}
  logging:
    console_enable: false
    console_level: INFO
    file_enable: false
    file_level: INFO
    file_retention: "7 days"
    file_prefix: null
"""

MINIMAL_MODULES = """
app_ingest_worker:
  max_content_length: 500
  min_content_length: 200
  item_name_chunk_k: 3
  item_name_chunk_size: 2500
  image_extensions: [.jpg, .png]

app_query_worker:
  query_high_threshold: 0.65
  query_mid_threshold: 0.55
  rerank_max_topk: 10
  rerank_min_topk: 3
  rerank_gap_abs: 0.5
  rerank_gap_ratio: 0.25
  max_context_chars: 12000

model_embedding_worker:
  backend: offline
  host: 0.0.0.0
  port: 8101
  max_batch_size: 64
  max_concurrency: 1
  offline: {model_path: /models/bge-m3, device: null, use_fp16: true, normalize_embeddings: true}
  online:
    api_key: ${oc.env:EMBEDDING_ONLINE_API_KEY,${oc.env:OPENAI_API_KEY,null}}
    base_url: http://emb.online/v1
    model: text-embedding-v4
    dimension: 1536
    timeout: 60

model_reranker_worker:
  backend: offline
  host: 0.0.0.0
  port: 8102
  max_documents: 128
  max_concurrency: 1
  offline: {model_path: /models/bge-reranker-large, device: null, use_fp16: true, normalize: true}
  online:
    api_key: ${oc.env:TEXT_RERANK_API_KEY,${oc.env:OPENAI_API_KEY,null}}
    model: qwen3-rerank
    instruct: 指令
"""


def _write_config_pkg(config_dir: Path, base_extra: str = "", env_content: str = "{}\n") -> None:
    (config_dir / "aipaas_config.base.yaml").write_text(
        MINIMAL_COMMON + MINIMAL_MODULES + base_extra, encoding="utf-8"
    )
    (config_dir / "aipaas_config.devcompany.yaml").write_text(env_content, encoding="utf-8")
    (config_dir / "aipaas_config.devhome.yaml").write_text("{}\n", encoding="utf-8")


class LoadSettingsTest(unittest.TestCase):
    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._saved_cwd = os.getcwd()
        settings_mod._reset_for_test()

    def tearDown(self):
        os.chdir(self._saved_cwd)
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        settings_mod._reset_for_test()

    def _prepare_tmp_pkg(self, base_extra: str = "", env_content: str = "{}\n") -> Path:
        tmp = Path(tempfile.mkdtemp())
        _write_config_pkg(tmp, base_extra, env_content)
        os.chdir(tmp)  # 避免 load_dotenv 拾取仓库真实 .env 造成用例不确定
        os.environ["CONFIG_DIR"] = str(tmp)
        os.environ["APP_ENV"] = "devcompany"
        return tmp

    # ---------- happy path：仓库真实配置包 ----------

    def test_happy_path_repo_configs_all_modules(self):
        os.environ.update({
            "CONFIG_DIR": str(REPO_CONFIG_DIR),
            "APP_ENV": "devcompany",
            "OPENAI_API_KEY": "sk-dummy",
            "MINIO_ACCESS_KEY": "ak",
            "MINIO_SECRET_KEY": "sk",
            "MONGO_URL": "mongodb://u:p@127.0.0.1:27017",
            "MINERU_API_TOKEN": "token",
        })
        os.environ.pop("EMBEDDING_ONLINE_API_KEY", None)
        os.environ.pop("TEXT_RERANK_API_KEY", None)

        for module, port in (("app_ingest_worker", None), ("app_query_worker", None),
                             ("model_embedding_worker", 8101), ("model_reranker_worker", 8102)):
            settings_mod._reset_for_test()
            s = load_settings(module)
            self.assertIs(get_settings(), s)
            self.assertEqual(s.common.minio.endpoint, "192.168.222.188:9000")
            self.assertEqual(s.common.llm.api_key_str, "sk-dummy")
            if port:
                self.assertEqual(s.module.port, port)
        # 模块字段抽查
        settings_mod._reset_for_test()
        self.assertEqual(load_settings("app_query_worker").module.query_mid_threshold, 0.55)
        settings_mod._reset_for_test()
        self.assertEqual(load_settings("app_ingest_worker").module.item_name_chunk_k, 3)

    def test_nested_api_key_fallback(self):
        os.environ.update({"OPENAI_API_KEY": "sk-main"})
        os.environ.pop("EMBEDDING_ONLINE_API_KEY", None)
        self._prepare_tmp_pkg()
        s = load_settings("model_embedding_worker")
        self.assertEqual(s.module.online.api_key_str, "sk-main")

        settings_mod._reset_for_test()
        os.environ["EMBEDDING_ONLINE_API_KEY"] = "sk-emb"
        s = load_settings("model_embedding_worker")
        self.assertEqual(s.module.online.api_key_str, "sk-emb")

    # ---------- 失败与边界场景 ----------

    def test_missing_config_dir(self):
        self._prepare_tmp_pkg()
        del os.environ["CONFIG_DIR"]
        with self.assertRaises(SettingsError):
            load_settings("app_query_worker")

    def test_invalid_app_env(self):
        self._prepare_tmp_pkg()
        os.environ["APP_ENV"] = "prod"
        with self.assertRaises(SettingsError):
            load_settings("app_query_worker")

    def test_unregistered_module(self):
        self._prepare_tmp_pkg()
        with self.assertRaises(SettingsError):
            load_settings("unknown_worker")

    def test_extra_key_forbidden(self):
        self._prepare_tmp_pkg(env_content="app_query_worker:\n  typo_key: 1\n")
        with self.assertRaises(ValidationError):
            load_settings("app_query_worker")

    def test_env_interpolation(self):
        self._prepare_tmp_pkg()
        os.environ["MINIO_ACCESS_KEY"] = "ak-from-env"
        s = load_settings("app_query_worker")
        self.assertEqual(s.common.minio.access_key_str, "ak-from-env")

    def test_crop_isolation_other_module_missing_secret(self):
        # app_ingest_worker 命名空间引用无默认值的缺失变量：加载 app_query_worker 不受影响，
        # 加载 app_ingest_worker 自身才应在 resolve 阶段失败
        self._prepare_tmp_pkg()
        base = Path(os.environ["CONFIG_DIR"]) / "aipaas_config.base.yaml"
        base.write_text(
            base.read_text(encoding="utf-8").replace(
                "max_content_length: 500", "max_content_length: ${oc.env:DEFINITELY_MISSING_VAR}"
            ),
            encoding="utf-8",
        )
        s = load_settings("app_query_worker")
        self.assertEqual(s.module.query_high_threshold, 0.65)

        settings_mod._reset_for_test()
        with self.assertRaises(InterpolationResolutionError):
            load_settings("app_ingest_worker")

    def test_double_load_same_module_cached_and_different_module_rejected(self):
        self._prepare_tmp_pkg()
        s1 = load_settings("app_query_worker")
        self.assertIs(load_settings("app_query_worker"), s1)
        with self.assertRaises(SettingsError):
            load_settings("app_ingest_worker")

    def test_get_settings_before_load_raises(self):
        with self.assertRaises(SettingsError):
            get_settings()


if __name__ == "__main__":
    unittest.main()
