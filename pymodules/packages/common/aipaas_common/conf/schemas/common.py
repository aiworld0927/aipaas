"""日志配置 schema：由 aipaas_config.*.yaml 的 common.logging 节点校验生成。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, SecretStr


class _Base(BaseModel):
    """所有配置模型基类：禁止未知字段，防止配置拼写错误静默生效。"""

    model_config = ConfigDict(extra="forbid")


class LLMSettings(_Base):
    """OpenAI 兼容 LLM 接口配置。"""

    base_url: str
    api_key: Optional[SecretStr] = None
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
    access_key: Optional[SecretStr] = None
    secret_key: Optional[SecretStr] = None

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
    url: Optional[SecretStr] = None
    db_name: str

    @property
    def url_str(self) -> str:
        return self.url.get_secret_value() if self.url else ""


class MineruSettings(_Base):
    base_url: str
    api_token: Optional[SecretStr] = None

    @property
    def api_token_str(self) -> str:
        return self.api_token.get_secret_value() if self.api_token else ""


class McpSettings(_Base):
    mcp_base_url: str
    api_key: Optional[SecretStr] = None

    @property
    def api_key_str(self) -> str:
        return self.api_key.get_secret_value() if self.api_key else ""


class HttpServiceSettings(_Base):
    """内部模型推理服务（embedding / reranker）HTTP 客户端配置。"""

    url: str
    timeout: float


class LoggingSettings(_Base):
    console_enable: bool
    console_level: str
    file_enable: bool
    file_level: str
    file_retention: str
    file_prefix: Optional[str] = None


class CommonSettings(_Base):
    """跨模块共享配置：所有子节点必须在 YAML 中存在。"""

    llm: LLMSettings
    milvus: MilvusSettings
    minio: MinioSettings
    mongo: MongoSettings
    mineru: MineruSettings
    mcp: McpSettings
    embedding_service: HttpServiceSettings
    rerank_service: HttpServiceSettings
    logging: LoggingSettings
