"""日志配置 schema：由 aipaas_config.*.yaml 的 common.logging 节点校验生成。"""
from pydantic import BaseModel, ConfigDict, SecretStr


class _Base(BaseModel):
    """所有配置模型基类：禁止未知字段，防止配置拼写错误静默生效。"""

    model_config = ConfigDict(extra="forbid")


class HostPortSettings(_Base):
    """所有 HTTP worker 共用的监听地址。

    端口统一由环境变量 `{MODULE_NAME大写}_PORT` / `{MODULE_NAME大写}_HOST` 覆盖
    （如 APP_INGEST_WORKER_PORT=9000），未设置时取 YAML 中的默认值。
    """

    host: str = "0.0.0.0"
    port: int


class LLMSettings(_Base):
    """OpenAI 兼容 LLM 接口配置。"""

    base_url: str
    api_key: SecretStr | None = None
    llm_model: str
    llm_temperature: float
    vl_model: str

    @property
    def api_key_str(self) -> str:
        return self.api_key.get_secret_value() if self.api_key else ""


class MilvusSettings(_Base):
    url: str
    chunks_collection: str
    item_name_collection: str
    entity_name_collection: str


class MinioSettings(_Base):
    """endpoint 为 host:port（不带 scheme），scheme 由 secure 推导。"""

    endpoint: str
    secure: bool
    bucket_name: str
    img_dir: str
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None

    @property
    def access_key_str(self) -> str:
        return self.access_key.get_secret_value() if self.access_key else ""

    @property
    def secret_key_str(self) -> str:
        return self.secret_key.get_secret_value() if self.secret_key else ""

    @property
    def base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"


class MongoSettings(_Base):
    url: SecretStr | None = None
    db_name: str

    @property
    def url_str(self) -> str:
        return self.url.get_secret_value() if self.url else ""


class MineruSettings(_Base):
    base_url: str
    api_token: SecretStr | None = None

    @property
    def api_token_str(self) -> str:
        return self.api_token.get_secret_value() if self.api_token else ""


class McpSettings(_Base):
    mcp_base_url: str
    api_key: SecretStr | None = None

    @property
    def api_key_str(self) -> str:
        return self.api_key.get_secret_value() if self.api_key else ""


class HttpServiceSettings(_Base):
    """内部模型推理服务（embedding / reranker）HTTP 客户端配置。"""
    url: str
    timeout: float


class NacosSettings(_Base):
    """Nacos 注册中心配置（业务 worker 启动时注册服务实例）。

    enabled 为 false 时跳过注册；注册失败只记 warning，不影响服务启动。
    register_ip 为空时由代码自动探测本机默认出口网卡地址。
    """

    enabled: bool = True
    server_address: str
    namespace: str = "public"
    group_name: str = "DEFAULT_GROUP"
    username: SecretStr | None = None
    password: SecretStr | None = None
    cluster_name: str = "DEFAULT"
    weight: float = 1.0
    register_ip: str | None = None
    log_level: str = "INFO"
    grpc_timeout_ms: int = 5000

    @property
    def username_str(self) -> str:
        return self.username.get_secret_value() if self.username else ""

    @property
    def password_str(self) -> str:
        return self.password.get_secret_value() if self.password else ""


class LoggingSettings(_Base):
    console_enable: bool
    console_level: str
    file_enable: bool
    file_level: str
    file_retention: str
    file_prefix: str | None = None


class CommonSettings(_Base):
    """跨模块共享配置：所有子节点必须在 YAML 中存在。"""

    llm: LLMSettings
    milvus: MilvusSettings
    minio: MinioSettings
    mongo: MongoSettings
    mineru: MineruSettings
    mcp: McpSettings
    # 未配置该节点视为不启用 Nacos（对应模块不会被加载）
    nacos: NacosSettings | None = None
    embedding_service: HttpServiceSettings
    rerank_service: HttpServiceSettings
    logging: LoggingSettings
