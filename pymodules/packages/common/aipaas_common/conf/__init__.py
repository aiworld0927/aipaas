"""统一配置入口：load_settings(module_name) / get_settings()。"""
from aipaas_common.conf.settings import SettingsError, get_settings, load_settings

__all__ = ["load_settings", "get_settings", "SettingsError"]
