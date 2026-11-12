# processor/query_processor/nodes/node_search_embedding.py
import asyncio

from aipaas_common.log_decorator import node_log
from aipaas_retrieval.milvus_utils import create_hybrid_search_requests, get_milvus_client, hybrid_search
from aipaas_common.conf.settings import get_settings
from aipaas_model_clients.lm.embedding_http_utils import agenerate_embeddings

from aipaas_common.core.log import logger
from query_worker.graph.state import QueryGraphState

@node_log("node_search_embedding")
async def node_search_embedding(state: QueryGraphState) -> QueryGraphState:
    """核心节点函数：Milvus 混合检索（异步版）"""
    try:
        query = state.get("rewritten_query")
        item_names = state.get("item_names")

        # 2、生成向量 (Dense + Sparse) — 走异步 httpx
        embeddings = await agenerate_embeddings([query])
        dense_vec = embeddings.get("dense")[0]
        sparse_vec = embeddings.get("sparse")[0]

        collection_name = get_settings().common.milvus.chunks_collection

        expr = f'item_name in {item_names}' if item_names else None
        if expr:
            logger.info(f"过滤条件: {expr}")
        else:
            logger.info("未指定商品名过滤，将全库检索")

        reqs = create_hybrid_search_requests(
            dense_vector=dense_vec,
            sparse_vector=sparse_vec,
            expr=expr,
            limit=10
        )

        logger.info("开始执行 Milvus 混合检索...")
        client = get_milvus_client()
        res = await asyncio.to_thread(
            hybrid_search,
            client=client,
            collection_name=collection_name,
            reqs=reqs,
            ranker_weights=(0.8, 0.2),
            output_fields=["chunk_id", "content", "item_name"],
        )
        return {"embedding_chunks": res[0] if res else []}

    except Exception as e:
        logger.exception(f"向量搜索失败: {e}")
        return {}


if __name__ == "__main__":
    import json
    from query_worker.graph.state import create_default_query_state

    init_state = create_default_query_state(
        original_query="H3CLA2608室内无线网关怎么使用？",
        rewritten_query="H3CLA2608室内无线网关怎么使用？",
        item_names=["H3CLA2608室内无线网关"],
    )
    result = asyncio.run(node_search_embedding(init_state))
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))

