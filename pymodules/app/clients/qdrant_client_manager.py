from typing import Optional, List
from qdrant_client import AsyncQdrantClient, models

from app.clients.embedding_client_manager import embedding_client_manager
from app.conf.app_config import QdrantConfig, app_config

# 用于搜索
from qdrant_client.models import Filter, FieldCondition, MatchValue

class QdrantClientManager:
    def __init__(self, qdrant_config: QdrantConfig):
        self.qdrant_config = qdrant_config
        self.client: Optional[AsyncQdrantClient] = None

    def _get_url(self):
        return f"http://{self.qdrant_config.host}:{self.qdrant_config.port}"

    def init(self):
        self.client = AsyncQdrantClient(url=self._get_url())

    async def close(self):
        await self.client.close()

    # 异步上下文管理器协议
    async def __aenter__(self):
        print(f"connect qdrant conf:{self._get_url()}")
        if self.client is None:
            self.init()
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"exiting qdrant conf:{self._get_url()}")
        await self.close()
        return False  # 不抑制异常


qdrant_client_manager = QdrantClientManager(app_config.qdrant)

if __name__ == '__main__':
    import asyncio

    qdrant_client_manager.init()
    client = qdrant_client_manager.client
    collection_name = "my_collection"

    async def test_collection(client: AsyncQdrantClient):
        if not await client.collection_exists(collection_name):
            print(f"创建集合：{collection_name}")
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=app_config.qdrant.embedding_size, distance=models.Distance.COSINE),
            )
        else:
            print(f"集合已经存在：{collection_name}")

    async def test_add_data(client: AsyncQdrantClient):
        keywords = ["苹果", "香蕉", "橘子", "芒果", "开封工程师", "测试工程师", "机器学习", "深度学习", "数据科学",
                    "数据处理", "数据可视化",
                    "汽车", "小米", "大米", "小米汽车", "小米手机", "华为手机", "龙牙外套", "北面外套"
        ]
        embedding_client = embedding_client_manager.init()
        batch_size = 5
        for i in range(0,len(keywords),batch_size):
            points = []
            embedding_batch = await embedding_client.aembed_documents(keywords[i:i+batch_size])
            for j,(keyword,embedding_word) in enumerate(zip(keywords[i:i+batch_size],embedding_batch),start=0):
                points.append(models.PointStruct(
                    id=i+j,
                    vector= embedding_word,
                    payload={"keyword":keyword}
                ))
                print(keyword)
            response = await client.upsert(collection_name=collection_name,wait=True,points=points)
            print(response)

    async def test_search(query,client: AsyncQdrantClient):
        embedding_client = embedding_client_manager.init()
        query_embed  = await embedding_client.aembed_query(query)
        res = await client.query_points(
            collection_name=collection_name,
            query=query_embed,  # type: ignore
            limit=10,
            score_threshold=0.6,
            query_filter=Filter(
                must=[FieldCondition(key="keyword", match=MatchValue(value="橘子"))]
            ),
        )
        print(res)
        print([point.payload for point in res.points])
        return [point.payload for point in res.points]

    # 创建集合
    # asyncio.run(test_collection(client=client))
    # asyncio.run(test_add_data(client=client))
    asyncio.run(test_search("西瓜",client=client))
