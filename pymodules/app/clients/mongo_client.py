import asyncio
from typing import Optional
from pymongo import AsyncMongoClient, MongoClient

from app.conf.mongo_config import MongoConfig, mongo_config


class MongoClientManager:
    def __init__(self,mongo_config:MongoConfig):
        self.mongo_config = mongo_config
        self.client:Optional[AsyncMongoClient] = None

    def _get_mongo_url(self):
        # 格式：mongodb://用户名:密码@主机:端口/数据库?authSource=认证库
        uri = f"mongodb://{self.mongo_config.mongo_username}:{self.mongo_config.mongo_password}@{self.mongo_config.mongo_host}:{self.mongo_config.mongo_port}/{self.mongo_config.mongo_database}?authSource=admin"
        return uri

    def init(self):
        # 同步 client = MongoClient(uri)
        self.client = AsyncMongoClient(
            self._get_mongo_url(),
        )
        return self.client

    async def test(self):
        try:
            if self.client is None:
                self.init()

            # 验证连接
            await self.client.admin.command("ping")
            print("异步连接成功")

            db = self.client["mydb"]
            collection = db["mycollection"]
            # 异步操作示例
            result = await collection.insert_one({"name": "Tom"})
        finally:
            await self.client.close()

    async def close(self):
        if self.client:
            await self.client.close()

mongo_client_manager = MongoClientManager(mongo_config)

if __name__ == '__main__':
    mongo_client_manager.init()
    async def async_test():
        await mongo_client_manager.test()
    asyncio.run(async_test())