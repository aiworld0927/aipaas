import asyncio
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker

from aipaas_common.conf.app_config import DBConfig, app_config


class MySQLClientManager:

    def __init__(self,db_config:DBConfig):
        self.db_config = db_config
        self.engine:Optional[AsyncEngine] = None
        self.session_factory = None

    def _get_url(self):
        return f"mysql+asyncmy://{self.db_config.user}:{self.db_config.password}@{self.db_config.host}:{self.db_config.port}/{self.db_config.database}?charset=utf8mb4"

    def init(self):
        self.engine = create_async_engine(url=self._get_url(),
                                          pool_size=10,
                                          pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine,
                                                  autoflush=True,
                                                  expire_on_commit=False,
                                                  autobegin=True)

    async def close(self):
        await self.engine.dispose()

    # 异步上下文管理器协议
    async def __aenter__(self):
        print(f"connect db conf:{self._get_url()}")
        if self.session_factory is None:
            self.init()
        self._session = self.session_factory()
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"exiting db conf:{self._get_url()}")
        try:
            if self._session is not None:
                # ★ 关键：由 manager 统一提交 / 回滚 / 关闭
                if exc_type is None:
                    await self._session.commit()
                else:
                    await self._session.rollback()
                await self._session.close()
        finally:
            self._session = None
            # 如果你希望每次退出都销毁连接池，就保留下面这行；
            # 如果是 Web 长驻服务，建议不要每次退出都 dispose，
            # 只在应用关闭时调用一次 close()。
            await self.close()
        return False  # 不抑制异常


iasp_mysql_client_manager = MySQLClientManager(app_config.db_iasp)
dw_mysql_client_manager = MySQLClientManager(app_config.db_dw)
meta_mysql_client_manager = MySQLClientManager(app_config.db_meta)

if __name__ == '__main__':
    iasp_mysql_client_manager.init()
    sql_text = text("show tables")
    async def test():
        async with iasp_mysql_client_manager.session_factory() as session:
            result = await session.execute(sql_text)
            rows = result.fetchall()
            print(rows)

    async def test_with_session():
        async with iasp_mysql_client_manager.session_factory() as session:
            result = await session.execute(sql_text)
            print(result.fetchall())

    # asyncio.run(test())
    asyncio.run(test_with_session())