
if __name__ == "__main__":
    from aipaas_common.conf.settings import load_settings
    # 配置引导：必须在导入业务模块前完成加载（唯一权威来源，失败即终止启动）
    settings = load_settings("model_embedding_worker")
    print(settings.module.is_online, flush=True)
    print("online" if settings.module.is_online else "offline", flush=True)
