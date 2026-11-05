import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from omegaconf import OmegaConf

from dotenv import load_dotenv

# 提前加载.env配置文件（全局执行一次即可，无需重复写）
load_dotenv()

# 日志配置
@dataclass
class File:
    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    enable: bool
    level: str


@dataclass
class LoggingConfig:
    file: File
    console: Console


# 数据库配置
@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class QdrantConfig:
    host: str
    port: int
    embedding_size: int


@dataclass
class EmbeddingConfig:
    host: str
    port: int
    model: str


@dataclass
class ESConfig:
    host: str
    port: int
    index_name: str


@dataclass
class LLMConfig:
    api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_BASE", ""))
    model_name: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_MODEL", ""))
    model_provider: str = field(default_factory=lambda: os.getenv("MODEL_PROVIDER", "deepseek"))



@dataclass
class AppConfig:
    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig


config_file = Path(__file__).parents[2] / "yaml" / "app_config.yaml"
context = OmegaConf.load(config_file)
schema = OmegaConf.structured(AppConfig)

app_config: AppConfig = cast(AppConfig, OmegaConf.to_object(OmegaConf.merge(schema, context)))


if __name__ == '__main__':
    print(os.getenv("DEEPSEEK_API_KEY", ""))
    print(app_config.llm.api_key)