"""配置加载原理演示：脚本自己实现一遍 OmegaConf 合并 + Pydantic 校验，
不调用 aipaas_common.conf.settings.load_settings，方便从零理解流程。

流程：
  1. load_dotenv()                 —— 读 .env（override=False，真实环境变量优先）
  2. OmegaConf.load(base + env)    —— 合并 aipaas_config.base.yaml + aipaas_config.{APP_ENV}.yaml
  3. OmegaConf.resolve(cropped)    —— 只在裁切后的子树上解析 ${oc.env:...} 插值
  4. OmegaConf.to_container        —— 转回原生 dict
  5. Pydantic model_validate       —— extra="forbid" 严格校验

注意：这是简化版，生产代码还会做进程缓存、环境变量覆盖端口、日志 sink 重建等。
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from omegaconf import OmegaConf

# 生产 schema（直接引用，保持与运行时一致；这里只做校验，不做缓存/端口覆盖）
from aipaas_common.conf.schemas import MODULE_SCHEMAS

# ---------- 基础配置 ----------
load_dotenv()
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", os.path.abspath("configs")))
APP_ENV = os.getenv("APP_ENV", "devcompany")

print("**" * 50)
print("OPENAI_API_KEY =", os.getenv("OPENAI_API_KEY"))
print("APP_ENV       =", APP_ENV)
print("CONFIG_DIR    =", CONFIG_DIR)
print("**" * 50)


def load_settings_demo(module_name: str):
    """简化版加载流程：base.yaml + {APP_ENV}.yaml → merge → crop → resolve → validate。"""

    base = OmegaConf.load(CONFIG_DIR / "aipaas_config.base.yaml")
    override_path = CONFIG_DIR / f"aipaas_config.{APP_ENV}.yaml"
    override = OmegaConf.load(override_path) if override_path.exists() else OmegaConf.create()

    merged = OmegaConf.merge(base, override)

    # 关键：生产代码 settings.py 只裁切 {common, module} 两层，
    # 这样 OmegaConf.resolve 只解析当前进程关心的命名空间，
    # 其他模块的缺失密钥（如只有 reranker 用的 TEXT_RERANK_API_KEY）不会误伤。
    cropped = OmegaConf.create({
        "common": merged["common"],
        "module": merged[module_name],
    })

    # OmegaConf.resolve 是插值解析的唯一真值来源：
    #   - ${oc.env:VAR}            → 取环境变量
    #   - ${oc.env:VAR, default}   → 环境变量缺失时回退 default
    #   - ${oc.env:A,${oc.env:B,null}} → 嵌套插值也正确处理
    # 任何解析失败（如 env 缺失且无默认值且不可为 null）会直接抛异常。
    OmegaConf.resolve(cropped)

    # 顺便做一遍显式校验（同样委托 OmegaConf，确保无残留占位符）
    _assert_no_unresolved(cropped)

    # 转原生 dict，丢给 Pydantic
    raw = OmegaConf.to_container(cropped, resolve=True)
    return MODULE_SCHEMAS[module_name].model_validate(raw)


def _assert_no_unresolved(cfg) -> None:
    """校验插值是否全部解析完成——把它交给 OmegaConf 自己做。

    早期版本用字符串扫描 "${" + "oc.env" 来检测，结果把所有插值都当错误：
      ${oc.env:NACOS_ENABLED,true}          ← 有默认值 true，env 缺失也 OK
      ${oc.env:NACOS_REGISTER_IP,null}      ← 注释写明 null=自动探测，是合法状态
      ${oc.env:API_KEY,${oc.env:API_K,null}} ← 嵌套插值，人工正则解析容易漏
    实际上 OmegaConf.resolve 已经把这些处理好了，这里只需要确认没有残留。
    """
    resolved = OmegaConf.to_container(cfg, resolve=True)
    # 递归检查：任何值里还包含 "${" 说明有占位符没被 resolve 吃掉
    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and "${" in node:
            raise RuntimeError(f"未解析的插值残留: {path} -> {node!r}")
    walk(resolved)


if __name__ == "__main__":
    for module_name, schema_cls in MODULE_SCHEMAS.items():
        try:
            settings = load_settings_demo(module_name)
            print(f"[OK] {module_name}: {type(settings).__name__}")
            common_keys = sorted(settings.common.model_dump().keys())
            module_keys = sorted(settings.module.model_dump().keys())
            print(f"     common = {common_keys[:6]}...")
            print(f"     module  = {module_keys[:6]}...")
        except Exception as e:
            print(f"[FAIL] {module_name}: {e}")
            raise
