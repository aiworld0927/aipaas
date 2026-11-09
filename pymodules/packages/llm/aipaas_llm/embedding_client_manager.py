from typing import Optional
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from aipaas_common.conf.app_config import EmbeddingConfig, app_config


class EmbeddingClientManager:
    def __init__( config: EmbeddingConfig):
        self.client: Optional[HuggingFaceEndpointEmbeddings] = None
        self.config = config

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        if self.client is None:
            self.client = HuggingFaceEndpointEmbeddings(model=self._get_url())
        return self.client


embedding_client_manager = EmbeddingClientManager(app_config.embedding)


if __name__ == '__main__':
    import asyncio
    # 创建 EmbeddedClientManager 实例
    client_manager = EmbeddingClientManager(app_config.embedding)

    client = client_manager.client
    if not client:
        # 初始化嵌入客户端
        client = client_manager.init()

    def test_sync_embedding():
        # 对文本 "hello world" 进行向量嵌入
        # query = client.client.embed_query("hello world")
        query = client.embed_documents(["苹果", "香蕉", "大鸭梨"])
        # 打印嵌入向量的长度
        print(len(query))
        # 打印嵌入向量的具体值
        print(query)

    async def test_async_embedding():
        # 对文本 "hello world" 进行向量嵌入
        # query = await client.aembed_query("hello world")
        query = await client.aembed_documents(["苹果", "香蕉", "大鸭梨"])
        # 打印嵌入向量的长度
        print(len(query))
        # 打印嵌入向量的具体值
        print(query)

    async def test_async_batch_embedding(batch_size: int=5):
        keywords = ["苹果","香蕉","橘子","芒果","开封工程师","测试工程师","机器学习","深度学习","数据科学","数据处理","数据可视化",
                    "汽车","小米","大米","小米汽车","小米手机","华为手机","龙牙外套","北面外套"
        ]
        for i in range(0, len(keywords), batch_size):
            batch_docs = await client.aembed_documents(keywords[i:i+batch_size])
            print(f"第一批向量化：{keywords[i:i+batch_size]}：：{batch_docs}")

    # 1、测试同步方法
    # test_sync_embedding()
    # 2、测试异步方法
    # asyncio.run(test_async_embedding())
    # 3、测试批量方法
    asyncio.run(test_async_batch_embedding())