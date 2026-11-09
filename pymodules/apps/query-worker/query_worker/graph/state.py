import copy
from typing import TypedDict, List

class QueryGraphState(TypedDict):
    """
    查询流程图状态
    包含整个查询流程中传递的所有数据。
    """

    session_id: str  # 会话ID
    message_id: str  # 消息ID

    original_query: str  # 用户原始问题

    # 检索过程中的中间数据
    embedding_chunks: list  # 普通向量检索回来的切片
    hyde_embedding_chunks: list  # 已向量化的假设性问题切片
    web_search_docs: list  # 网络搜索回来的文档

    # 排序过程中的数据
    rrf_chunks: list  # RRF 融合排序后的切片
    reranked_docs: list  # 重排序后的最终 Top-K 文档

    # 生成过程中的数据
    prompt: str  # 组装好的 Prompt
    answer: str  # 最终生成的答案

    # 辅助信息
    item_names: List[str]  # 提取出的商品名称
    rewritten_query: str  # 改写后的问题
    history: list  # 历史对话记录
    is_stream: bool  # 是否流式输出
    image_urls: List[str]


QUERY_DEFAULT_STATE: QueryGraphState = {
    "session_id": "",
    "message_id": "",
    "original_query": "",

    "embedding_chunks": [],
    "hyde_embedding_chunks": [],
    "web_search_docs": [],
    "rrf_chunks": [],
    "reranked_docs": [],

    "prompt": "",
    "answer": "",

    "item_names": [],
    "rewritten_query": "",
    "history": [],
    "is_stream": False,
    "image_urls":[]
}


def create_default_query_state(**overrides) -> QueryGraphState:
    """
    创建默认状态，支持覆盖
    Args:
        **overrides: 要覆盖的字段
    Returns:
        新的状态实例
    Examples:
    """
    state = copy.deepcopy(QUERY_DEFAULT_STATE)
    state.update(overrides)
    return state