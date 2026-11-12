"""统一配置加载器。

加载流程（load_settings）：
1. load_dotenv()：本地开发读取 .env（override=False，真实环境变量优先）
2. 校验 module_name 已注册（= Python 包名）
3. CONFIG_DIR 强制从环境变量读取，代码不提供默认值
4. APP_ENV 强制校验：devhome / devcompany
5. 合并 aipaas_config.base.yaml → aipaas_config.{APP_ENV}.yaml（两文件必须都存在）
6. 先裁切：只保留 common + 本模块命名空间
7. 后 resolve：${oc.env:...} 插值只在裁切后的子树上解析，
   其他模块引用的缺失密钥不影响本进程
8. Pydantic 校验（extra="forbid"，密钥为 Optional[SecretStr]，消费方运行时校验）
9. 按进程缓存；同进程换 module_name 报错
10. 副作用：按 common.logging + module_name 推导的前缀重建日志 sink，
    并打印裁切后的键集合（不含值，天然掩码）

packages 等无 module_name 感知的共享代码使用 get_settings() 获取已加载配置。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel

from aipaas_common.conf.schemas import MODULE_SCHEMAS

VALID_APP_ENVS = ("devhome", "devcompany","dev_win11","dev_win10")
BASE_CONFIG_FILE = "aipaas_config.base.yaml"


class SettingsError(RuntimeError):
    """配置加载失败：环境变量缺失 / 配置文件缺失 / 命名空间未注册 / 进程内重复加载。"""


_settings: BaseModel | None = None
_loaded_module: str | None = None


def load_settings(module_name: str) -> BaseModel:
    """加载并校验本进程的配置，返回对应模块的 Settings 模型实例。"""
    global _settings, _loaded_module

    _ = load_dotenv()

    if module_name not in MODULE_SCHEMAS:
        raise SettingsError(f"未注册的 module_name：{module_name!r}，可选：{sorted(MODULE_SCHEMAS)}")
    if _settings is not None:
        if module_name != _loaded_module:
            raise SettingsError(f"本进程已按 {_loaded_module!r} 加载配置，不能再以 {module_name!r} 加载")
        return _settings

    config_dir_str = os.environ.get("CONFIG_DIR", "/home/aipaas/aipaas/pymodules/configs").strip()
    
    if not config_dir_str:
        raise SettingsError("缺少环境变量 CONFIG_DIR（配置包目录，代码不提供默认值）")
    
    app_env = os.environ.get("APP_ENV", "devhome").strip()
    if app_env not in VALID_APP_ENVS:
        raise SettingsError(f"APP_ENV 非法：{app_env!r}，可选：{list(VALID_APP_ENVS)}")

    config_dir = Path(config_dir_str)
    base_path = config_dir / BASE_CONFIG_FILE
    env_path = config_dir / f"aipaas_config.{app_env}.yaml"
    for path in (config_dir, base_path, env_path):
        if not path.exists():
            raise SettingsError(f"配置路径不存在：{path}")

    merged = OmegaConf.merge(OmegaConf.load(base_path), OmegaConf.load(env_path))
    if "common" not in merged:
        raise SettingsError(f"配置缺少 common 命名空间：{base_path}")
    if module_name not in merged:
        raise SettingsError(f"配置缺少模块命名空间 {module_name!r}：{base_path}")

    cropped = OmegaConf.create({"common": merged["common"], "module": merged[module_name]})
    OmegaConf.resolve(cropped)
    data = OmegaConf.to_container(cropped, resolve=True)
    _apply_env_overrides(data, module_name)

    settings = MODULE_SCHEMAS[module_name].model_validate(data)

    _apply_logging(settings, module_name)
    _settings, _loaded_module = settings, module_name
    _log_loaded_keys(settings, module_name, app_env, config_dir)
    return settings


def get_settings() -> BaseModel:
    """获取本进程已加载的配置（供共享包使用，无需感知 module_name）。"""
    if _settings is None:
        raise SettingsError("配置尚未加载：请先由模块入口调用 load_settings(module_name)")
    return _settings


def _apply_env_overrides(data: dict, module_name: str) -> None:
    """统一用环境变量覆盖监听地址，优先级：环境变量 > {APP_ENV}.yaml > base.yaml。

    变量名固定为 `{MODULE_NAME大写}_{字段大写}`：
      INGEST_WORKER_PORT / QUERY_WORKER_PORT /
      MODEL_EMBEDDING_WORKER_PORT / MODEL_RERANKER_WORKER_PORT（另有对应 _HOST）
    这样容器、裸机、K8s 都只需改环境变量即可换端口，无需改 YAML 或镜像。
    """
    module = data.setdefault("module", {})
    prefix = module_name.upper()
    for field in ("host", "port"):
        raw = os.environ.get(f"{prefix}_{field.upper()}", "").strip()
        if not raw:
            continue
        if field == "port":
            try:
                module[field] = int(raw)
            except ValueError:
                raise SettingsError(f"环境变量 {prefix}_PORT 必须是整数，当前：{raw!r}") from None
        else:
            module[field] = raw


def _apply_logging(settings: BaseModel, module_name: str) -> None:
    from aipaas_common.core.log import apply_log_config

    prefix = settings.common.logging.file_prefix or module_name.replace("_", "-")
    apply_log_config(settings.common.logging, prefix)


def _log_loaded_keys(settings: BaseModel, module_name: str, app_env: str, config_dir: Path) -> None:
    from aipaas_common.core.log import logger

    common_keys = sorted(settings.common.model_dump().keys())
    module_keys = sorted(settings.module.model_dump().keys())
    logger.info(
        f"配置加载完成 | module={module_name} env={app_env} dir={config_dir} "
        f"common_keys={common_keys} module_keys={module_keys}"
    )


def _reset_for_test() -> None:
    """仅供单元测试重置进程缓存。"""
    global _settings, _loaded_module
    _settings, _loaded_module = None, None

if __name__ == "__main__":
    settings = load_settings("model_embedding_worker")
    print(settings)