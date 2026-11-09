# processor/query_processor/nodes/node_search_embedding_hyde.py
from aipaas_common.conf.milvus_config import milvus_config
from aipaas_common.core.load_prompt import load_prompt
from aipaas_common.core.log import logger
from aipaas_common.log_decorator import node_log
from aipaas_llm.lm.embedding_utils import generate_embeddings
from aipaas_llm.lm.lm_utils import get_llm_client
from aipaas_retrieval.milvus_utils import hybrid_search, get_milvus_client, create_hybrid_search_requests
from query_worker.graph.state import QueryGraphState

@node_log("node_search_embedding_hyde")
def node_search_embedding_hyde( state: QueryGraphState) -> QueryGraphState:
    """
    HyDE (Hypothetical Document Embedding) 检索节点
    核心思想：通过LLM生成假设性答案（HyDE文档），将其向量化后用于检索，以解决短查询语义稀疏问题。

    执行步骤：
    1. 参数提取：从会话状态中获取改写后的查询（rewritten_query）和已确认的商品名（item_names）。
    2. 生成假设文档 (Step 1)：调用LLM，基于用户问题生成一段假设性的理想回答（即HyDE文档）。
    3. 混合检索 (Step 2)：
       - 将“用户问题 + 假设文档”合并，生成BGE-M3稠密+稀疏向量。
       - 在Milvus中执行混合检索（带商品名过滤），召回最相似的知识切片。
    4. 结果封装：返回检索到的切片列表和生成的假设文档，更新会话状态。

    :param state: 会话状态字典，包含 rewritten_query, item_names 等
    :return: 包含 hyde_embedding_chunks (检索结果) 和 hyde_doc (假设文档) 的字典
    """

    # 1、用户问题和已确认商品名
    rewritten_query = state.get("rewritten_query")
    item_names = state.get("item_names")

    try:

        # 2、生成假设性文档
        hyde_doc = _step_1_create_hyde_doc(rewritten_query)

        # 3、用“重写问题 + 假设文档”检索切片
        res = _step_2_search_embedding_hyde(
            rewritten_query=rewritten_query,
            hyde_doc=hyde_doc,
            item_names=item_names
        )

        # 4、结果封装
        return {"hyde_embedding_chunks": res, "hyde_doc": hyde_doc}
    except Exception as e:
        logger.exception(f"假设性文档向量搜索失败: {e}")
        return {}

def _step_1_create_hyde_doc( rewritten_query: str) -> str:
    """
    阶段1：利用大模型根据用户查询生成假设性文档（Hypothetical Document）。
    HyDE的核心在于：利用LLM生成一个“虚构但相关”的文档，用该文档的向量去检索真实的文档，
    从而缓解短查询（Query）与长文档（Document）在语义空间不匹配的问题。

    :param rewritten_query: 用户改写后的查询语句
    :return: LLM生成的假设性文档内容
    """

    logger.info("步骤1: 开始生成假设性文档")

    try:
        llm = get_llm_client()
        hyde_prompt = load_prompt("hyde_prompt", rewritten_query=rewritten_query)
        hyde_doc = llm.invoke(hyde_prompt).content
        return hyde_doc

    except Exception as e:
        logger.exception(f"步骤1: 生成假设文档失败: {e}")
        raise e

def _step_2_search_embedding_hyde(

        rewritten_query: str,
        hyde_doc: str,
        item_names=None
):
    """
    阶段2：利用“重写问题 + 假设性文档”生成 embedding，并到向量库检索切片。

    :param rewritten_query: 改写后的查询
    :param hyde_doc: Step 1 生成的假设性文档
    :param item_names: 商品名称列表，用于元数据过滤 (item_name in [...])
    :return: 检索结果列表
    """

    try:
        # 1、拼接查询与假设文档，形成更丰富的语义上下文
        # 这里把用户问题 + 假设答案拼在一起生成向量，相当于：
        # 既保留了用户的原始意图（rewritten_query）
        # 又增强了语义丰富度（hyde_doc）
        combined_text = rewritten_query + " " + hyde_doc

        # 2、生成向量 (Dense + Sparse)
        embeddings = generate_embeddings([combined_text])
        dense_vec = embeddings.get("dense")[0]
        sparse_vec = embeddings.get("sparse")[0]

        # 3. 获取Milvus的集合
        collection_name = milvus_config.chunks_collection

        # 4、处理 item_names 中的引号，防止注入或语法错误
        expr = None
        if item_names:
            # quoted = ", ".join(f'"{v}"' for v in item_names)
            # expr = f"item_name in [{quoted}]"
            # 'item_name in ["BrotherHAK-180烫金机","BrotherHAK180烫金机"]'
            expr = f'item_name in {item_names}'
            logger.info(f"步骤2: 过滤条件: {expr}")
        else:
            logger.info("步骤2: 未指定商品名过滤，将全库检索")

        # 5、构造Milvus混合搜索请求对象
        reqs = create_hybrid_search_requests(
            dense_vector=dense_vec,
            sparse_vector=sparse_vec,
            expr=expr,
            limit=10  # 底层检索返回数量（后续会再过滤为5，预留更多结果做重排序）
        )

        # 6、执行混合向量检索
        logger.info("步骤2: 开始执行 Milvus 混合检索...")
        client = get_milvus_client()
        res = hybrid_search(
            client=client,
            collection_name=collection_name,
            reqs=reqs,
            ranker_weights=(0.8, 0.2),
            output_fields=["chunk_id", "content", "item_name"],
        )

        return res[0] if res else []

    except Exception as e:
        logger.error(f"步骤2: 检索过程发生异常: {e}")
        raise e


if __name__ == "__main__":
    import json
    from query_worker.graph.state import create_default_query_state

    # 初始化图状态
    init_state = {
        "original_query": "H3CLA2608室内无线网关怎么使用？",
        "rewritten_query": "H3CLA2608室内无线网关怎么使用？",
        "item_names": ["H3CLA2608室内无线网关"],
    }
    init_state = create_default_query_state(**init_state)
    # 执行节点的单元测试
    result = node_search_embedding_hyde(init_state)
    # 输出:将返回的图状态进行json序列化
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))