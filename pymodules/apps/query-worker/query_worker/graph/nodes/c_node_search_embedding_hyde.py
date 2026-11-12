# processor/query_processor/nodes/node_search_embedding_hyde.py
import asyncio

from aipaas_common.conf.settings import get_settings
from aipaas_common.core.load_prompt import load_prompt
from aipaas_common.core.log import logger
from aipaas_common.log_decorator import node_log
from aipaas_model_clients.lm.embedding_http_utils import agenerate_embeddings
from aipaas_model_clients.lm.lm_utils import get_llm_client
from aipaas_retrieval.milvus_utils import hybrid_search, get_milvus_client, create_hybrid_search_requests
from query_worker.graph.state import QueryGraphState

@node_log("node_search_embedding_hyde")
async def node_search_embedding_hyde(state: QueryGraphState) -> QueryGraphState:
    """HyDE 检索节点（异步版）"""
    rewritten_query = state.get("rewritten_query")
    item_names = state.get("item_names")

    try:
        hyde_doc = await _step_1_create_hyde_doc(rewritten_query)
        res = await _step_2_search_embedding_hyde(
            rewritten_query=rewritten_query,
            hyde_doc=hyde_doc,
            item_names=item_names,
        )
        return {"hyde_embedding_chunks": res, "hyde_doc": hyde_doc}
    except Exception as e:
        logger.exception(f"假设性文档向量搜索失败: {e}")
        return {}


async def _step_1_create_hyde_doc(rewritten_query: str) -> str:
    """利用大模型生成假设性文档（异步版）"""
    logger.info("步骤1: 开始生成假设性文档")
    try:
        llm = get_llm_client()
        hyde_prompt = load_prompt("hyde_prompt", rewritten_query=rewritten_query)
        hyde_doc = await llm.ainvoke(hyde_prompt)
        return hyde_doc.content
    except Exception as e:
        logger.exception(f"步骤1: 生成假设文档失败: {e}")
        raise


async def _step_2_search_embedding_hyde(rewritten_query: str, hyde_doc: str, item_names=None):
    """拼接 HyDE 文档后向量检索（异步版）"""
    try:
        combined_text = rewritten_query + " " + hyde_doc
        embeddings = await agenerate_embeddings([combined_text])
        dense_vec = embeddings.get("dense")[0]
        sparse_vec = embeddings.get("sparse")[0]

        collection_name = get_settings().common.milvus.chunks_collection

        expr = f'item_name in {item_names}' if item_names else None
        if expr:
            logger.info(f"步骤2: 过滤条件: {expr}")
        else:
            logger.info("步骤2: 未指定商品名过滤，将全库检索")

        reqs = create_hybrid_search_requests(
            dense_vector=dense_vec,
            sparse_vector=sparse_vec,
            expr=expr,
            limit=10,
        )

        logger.info("步骤2: 开始执行 Milvus 混合检索...")
        client = get_milvus_client()
        res = await asyncio.to_thread(
            hybrid_search,
            client=client,
            collection_name=collection_name,
            reqs=reqs,
            ranker_weights=(0.8, 0.2),
            output_fields=["chunk_id", "content", "item_name"],
        )
        return res[0] if res else []

    except Exception as e:
        logger.error(f"步骤2: 检索过程发生异常: {e}")
        raise


if __name__ == "__main__":
    import json
    from query_worker.graph.state import create_default_query_state

    init_state = create_default_query_state(
        original_query="H3CLA2608室内无线网关怎么使用？",
        rewritten_query="H3CLA2608室内无线网关怎么使用？",
        item_names=["H3CLA2608室内无线网关"],
    )
    result = asyncio.run(node_search_embedding_hyde(init_state))
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))