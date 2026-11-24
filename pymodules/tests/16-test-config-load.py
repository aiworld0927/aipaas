"""配置加载流程演示 / 自测脚本。

对应 app-ingest-worker 入口第 29 行的 ``settings = get_ingest_settings()``，
底层调用链是：

    app.py -> get_ingest_settings() -> load_settings("app_ingest_worker")

本脚本逐项打印 load_settings 的 10 个步骤，便于理解配置是怎么"合起来"的：

  1. load_dotenv()：读取 .env（override=False，真实环境变量优先）
  2. 校验 module_name 已注册（必须是 Python 包名）
  3. 读取 CONFIG_DIR（配置目录）与 APP_ENV（环境名）
  4. 合并 base.yaml -> {APP_ENV}.yaml（后者覆盖前者）
  5. 裁切：只保留 common + 本模块命名空间
  6. 解析 ${oc.env:VAR,默认值} 环境变量插值
  7. 环境变量覆盖 host/port（如 APP_INGEST_WORKER_PORT）
  8. Pydantic 校验（extra="forbid"，配错字段直接报错）
  9. 进程内缓存（同一进程重复调用返回同一对象）
 10. 按配置重建日志 sink

运行方式：

    cd /home/aipaas/aipaas/pymodules
    CONFIG_DIR=configs APP_ENV=devhome .venv/bin/python tests/16-test-config-load.py
"""
from __future__ import annotations

import os
from pathlib import Path

from aipaas_common.conf.settings import (
    SettingsError,
    _reset_for_test,
    get_settings,
    load_settings,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]


def title(index: str, text: str) -> None:
    print(f"\n{'=' * 72}\n[{index}] {text}\n{'=' * 72}")


def ok(label: str, value: object) -> None:
    print(f"  [OK]   {label}: {value}")


def masked(label: str, secret: str) -> None:
    """密钥只打印是否配置 + 长度，避免把明文写进终端/日志。"""
    if secret:
        ok(label, f"已配置（SecretStr，长度 {len(secret)}，值已掩码）")
    else:
        ok(label, "未配置（None）")


def expect_error(label: str, func) -> None:
    try:
        func()
    except SettingsError as exc:
        ok(label, f"按预期失败 -> {exc}")
    except Exception as exc:  # 非配置类异常说明代码有问题
        print(f"  [FAIL] {label}: 抛出非 SettingsError -> {type(exc).__name__}: {exc}")
    else:
        print(f"  [FAIL] {label}: 预期报错，但调用成功了")


def main() -> None:
    print(f"工作目录        : {Path.cwd()}")
    print(f"配置目录(CONFIG_DIR): {os.environ.get('CONFIG_DIR', '<未设置>')}")
    print(f"环境名(APP_ENV)     : {os.environ.get('APP_ENV', '<未设置>')}")
    print(f".env 文件           : {PROJECT_DIR / '.env'}（存在={((PROJECT_DIR / '.env').exists())}）")

    # ---------------------------------------------------------------- 1
    title("1", "加载前直接 get_settings()：必须报错（强制入口先加载配置）")
    _reset_for_test()
    expect_error("get_settings() before load", get_settings)

    # ---------------------------------------------------------------- 2
    title("2", "模块名未注册：必须报错并列出可选模块")
    expect_error("load_settings('not_exists')", lambda: load_settings("not_exists"))

    # ---------------------------------------------------------------- 3
    title("3", "正常加载：环境变量覆盖 host/port 后加载 app_ingest_worker")
    os.environ["APP_INGEST_WORKER_HOST"] = "127.0.0.1"
    os.environ["APP_INGEST_WORKER_PORT"] = "9101"

    base_file = Path(os.environ["CONFIG_DIR"]) / "aipaas_config.base.yaml"
    env_file = Path(os.environ["CONFIG_DIR"]) / f"aipaas_config.{os.environ['APP_ENV']}.yaml"
    print(f"  合并顺序: {base_file.name} -> {env_file.name}（后者覆盖前者）")

    settings = load_settings("app_ingest_worker")
    ok("返回类型", type(settings).__name__)
    ok("顶层键（裁切结果）", sorted(settings.model_dump().keys()))

    print("\n  -- 本模块命名空间（module）--")
    for key in ("host", "port", "max_content_length", "min_content_length",
                "item_name_chunk_k", "item_name_chunk_size"):
        ok(f"module.{key}", getattr(settings.module, key))
    ok("module.image_extensions 类型", type(settings.module.image_extensions).__name__)
    ok("module.image_extensions 值", sorted(settings.module.image_extensions))

    print("\n  -- 公共命名空间（common）典型字段 --")
    ok("common.llm.base_url", settings.common.llm.base_url)
    ok("common.llm.llm_model", settings.common.llm.llm_model)
    ok("common.milvus.url", settings.common.milvus.url)
    ok("common.minio.endpoint", settings.common.minio.endpoint)
    ok("common.embedding_service.url（${oc.env:...} 已解析）",
       settings.common.embedding_service.url)
    ok("common.nacos.enabled", settings.common.nacos.enabled if settings.common.nacos else None)
    ok("common.nacos.server_address（环境变量优先）",
       settings.common.nacos.server_address if settings.common.nacos else None)
    masked("common.llm.api_key", settings.common.llm.api_key_str)
    masked("common.minio.access_key", settings.common.minio.access_key_str)
    masked("common.mongo.url", settings.common.mongo.url_str)
    if settings.common.nacos:
        masked("common.nacos.password", settings.common.nacos.password_str)

    # ---------------------------------------------------------------- 4
    title("4", "限定了生效范围：只能看到 common + app_ingest_worker")
    ok("含 app_query_worker 命名空间", "app_query_worker" in settings.model_dump())
    ok("module 含 query_high_threshold", hasattr(settings.module, "query_high_threshold"))

    # ---------------------------------------------------------------- 5
    title("5", "进程内缓存：重复 load_settings 返回同一对象")
    again = load_settings("app_ingest_worker")
    ok("settings is again", settings is again)

    # ---------------------------------------------------------------- 6
    title("6", "同进程换模块：必须报错（配置按进程单例）")
    expect_error("load_settings('app_query_worker')", lambda: load_settings("app_query_worker"))

    # ---------------------------------------------------------------- 7
    title("7", "非法 APP_ENV：必须报错")
    _reset_for_test()
    try:
        os.environ["APP_ENV"] = "prod"
        expect_error("APP_ENV=prod", lambda: load_settings("app_ingest_worker"))
    finally:
        os.environ["APP_ENV"] = "devhome"

    # ---------------------------------------------------------------- 8
    title("8", "端口环境变量不是整数：必须报错")
    _reset_for_test()
    try:
        os.environ["APP_INGEST_WORKER_PORT"] = "abc"
        expect_error("APP_INGEST_WORKER_PORT=abc", lambda: load_settings("app_ingest_worker"))
    finally:
        os.environ["APP_INGEST_WORKER_PORT"] = "9101"

    # ---------------------------------------------------------------- 9
    title("9", "配置目录不存在：必须报错")
    _reset_for_test()
    try:
        os.environ["CONFIG_DIR"] = "/tmp/aipaas-config-not-exists"
        expect_error("CONFIG_DIR=/tmp/aipaas-config-not-exists",
                     lambda: load_settings("app_ingest_worker"))
    finally:
        os.environ["CONFIG_DIR"] = "configs"

    # ---------------------------------------------------------------- 10
    title("10", "环境差异覆盖：devhome vs devcompany 的模型路径")
    _reset_for_test()
    devhome = load_settings("model_embedding_worker")
    ok("devhome offline.model_path", devhome.module.offline.model_path)
    ok("devhome offline.device", devhome.module.offline.device)

    _reset_for_test()
    try:
        os.environ["APP_ENV"] = "devcompany"
        devcompany = load_settings("model_embedding_worker")
        ok("devcompany offline.model_path", devcompany.module.offline.model_path)
        ok("devcompany offline.device", devcompany.module.offline.device)
    finally:
        os.environ["APP_ENV"] = "devhome"

    print("\n全部演示结束。以上 [OK] 表示行为符合预期，[FAIL] 表示配置加载逻辑异常。")


if __name__ == "__main__":
    main()
