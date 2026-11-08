import importlib
import os
from pathlib import Path

from fastapi import FastAPI
from app.core.log import logger
from app.apis.aipaas_lifespan import lifespan

app = FastAPI(title="AIPaaS访问入口", lifespan=lifespan)

def _dynamic_load_routes(app):
    """动态加载，routers目录下路由分发
    说明：
        1、获取模块列表
        2、加载各个模块
    """
    app.state.conf = {"hello": "world"}
    router_path = Path(__file__).parent / "routers"
    model_list = [route.stem for route in router_path.glob("*.py") if not route.name.startswith('__')]
    logger.info(f"动态加载:model_list: {model_list}")
    logger.info(f"pid={os.getpid()}, module_name={__name__}")
    for mod_name in model_list:
        try:
            _mod = importlib.import_module(f"app.apis.routers.{mod_name}")
            if hasattr(_mod, 'router'):
                logger.info(" --begin register module {}", mod_name)
                route = getattr(_mod, 'router')
                # 与原接口一致：把 app（FastAPI 实例）与 conf 传进去
                # app.include_router(_mod.router)
                app.include_router(route)
                logger.info("module {} registered", mod_name)
            else:
                logger.warning(f" module {mod_name} has no router")
        except Exception as x:
            logger.error(" import module {} failed {}", mod_name, x)
            continue

_dynamic_load_routes(app)

# 主应用自身也可以定义路由
@app.get("/")
async def root():
    return {"message": "欢迎访问主页面"}

if __name__ == "__main__":

    import uvicorn
    # 直接在代码中启动uvicorn服务器
    uvicorn.run(
        app="aipaas_main:app",  # 指定要运行的FastAPI应用实例:      备注:字符串形式，uvicorn 会再 import 一次  app=app
        host="0.0.0.0",         # 允许外部访问（本地可通过127.0.0.1或localhost访问）
        port=8000,              # 端口号
        reload=True             # 开发模式：代码修改后自动重启（生产环境需去掉）
    )
    """
    
    """
