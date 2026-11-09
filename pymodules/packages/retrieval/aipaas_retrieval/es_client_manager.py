import asyncio
from typing import Optional

from elasticsearch import AsyncElasticsearch
from aipaas_common.conf.app_config import ESConfig, app_config


class ESClientManager:
    def __init__(self,es_config: ESConfig):
        self.es_config = es_config
        self.client: Optional[AsyncElasticsearch] = None

    def _get_url(self):
        return f"http://{self.es_config.host}:{self.es_config.port}"

    def init(self):
        self.client = AsyncElasticsearch(hosts=[self._get_url()])
        return self.client

    async def close(self):
        await self.client.close()

    # 异步上下文管理器协议
    async def __aenter__(self):
        print(f"connect elasticsearch conf:{self._get_url()}")
        if self.client is None:
            self.init()
        return self.client

    async def __aexit__(self,exc_type, exc_val, exc_tb):
        print(f"exiting elasticsearch conf:{self._get_url()}")
        await self.close()
        return False  # 不抑制异常

es_client_manager = ESClientManager(app_config.es)

if __name__ == '__main__':

    index_name = "goods"
    mappings = {
        "dynamic":False,
        "properties": {
            "id":{
                "type": "long"
            },
            "name":{
                "type": "text",
                "analyzer": "ik_max_word"
            },
            "brand": {
                "type": "keyword"
            },
            "image":{
                "type": "keyword",
                "index": False
            },
            "price":{
                "type": "float"
            }
        }
    }

    async def test_index_own_close():
        es_client = es_client_manager.init()
        if await es_client.indices.exists(index=index_name):
            print(f"index {index_name} exists")
            await es_client_manager.close()
            return
        resp = await es_client.indices.create(
            index=index_name,
            mappings=mappings,
        )
        await es_client_manager.close()
        print(resp)


    async def test_index():
        async with es_client_manager.init() as es_client:
            if await es_client.indices.exists(index=index_name):
                print(f"index {index_name} exists")
                return
            resp = await es_client.indices.create(
                index=index_name,
                mappings=mappings,
            )
            print(resp)

    async def test_delete_index():
        async with es_client_manager.init() as es_client:
            await es_client.indices.delete(index=index_name)

    async def test_add_one_data():
        async with es_client_manager.init() as es_client:
            await es_client.index(  # 新增或覆盖
            # await client.create( # 只新增
                index=index_name,
                id = "1",
                document={
                    "id": 1, "name": "小米su7 Ultra", "brand": "小米汽车", "price": 217000.00
                }
            )

    async def test_add_batch_data():
        async with es_client_manager.init() as es_client:
            # 插入数据
            await es_client.bulk(
                index=index_name,
                operations=[
                    {"index": {"_id": 3}},
                    {"id": 3, "name": "小米17ProMax，雪花白", "brand": "小米", "price": 5000.00},
                    {"create": {"_id": 4}},
                    {"id": 4, "name": "小米17Pro，黑色", "brand": "小米", "price": 4500.00}
                ],
            )

    async def test_search_analyzer():
        async with es_client_manager.init() as es_client:
            # 1、分词搜索
            resp = await es_client.search(
                index=index_name,
                query={
                    "match": {
                        "name": "我喜欢吃小米，家里的麦子熟了"
                    }
                },
            )
            parse_resp(resp)

    async def test_search_shot():
        async with es_client_manager.init() as es_client:
            # 精准检索
            resp = await es_client.search(
                index=index_name,
                query={
                    "term": {
                        "brand": {
                            "value": "小米汽车"
                        }
                    }
                }
            )
            parse_resp(resp)



    def parse_resp(resp):
        print(resp)
        # 1) 取总命中数
        total = resp["hits"]["total"]["value"]
        print(f"命中结果数:{total}")
        # 2) 遍历所有命中的文档
        for hit in resp["hits"]["hits"]:
            doc_id = hit["_id"]
            score = hit["_score"]
            source = hit["_source"]  # 这是你写入的原始 dict
            print(f"文档id:{doc_id}, score:{score}, source:{source}")


    # 1、检测创建索引库
    # asyncio.run(test_index_own_close())
    asyncio.run(test_index())
    # asyncio.run(test_delete_index())
    # 2、新增一条记录
    # asyncio.run(test_add_one_data())
    # 3、批量新增
    # asyncio.run(test_add_batch_data())
    # 3、索引检索功能
    # asyncio.run(test_search_analyzer())
    # asyncio.run(test_search_shot())

