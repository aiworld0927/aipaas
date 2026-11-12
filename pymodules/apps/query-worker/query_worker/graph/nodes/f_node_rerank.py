# processor/query_processor/nodes/node_rerank.py
import asyncio
import json
from typing import List, Dict, Any

from aipaas_common.core.log import logger
from aipaas_common.log_decorator import node_log
from aipaas_model_clients.lm.reranker_http_utils import arerank_documents
from aipaas_common.conf.settings import get_settings
from query_worker.graph.state import QueryGraphState


@node_log("node_rerank")
async def node_rerank(state: QueryGraphState) -> QueryGraphState:
    """Cross-Encoder 对 RRF 结果精排（异步版）"""

    merged_multi_docs: List[Dict[str, Any]] = _step_1_merge_multi_source_docs(state)
    reranked_docs: List[Dict[str, Any]] = await _step_2_rerank_merged_docs(state, merged_multi_docs)
    cutoff_docs = _step_3_cliff_cutoff(reranked_docs)

    state["reranked_docs"] = cutoff_docs
    return state


def _step_1_merge_multi_source_docs(state: QueryGraphState) -> List[Dict[str, Any]]:
    """合并本地 RRF 结果和网络搜索结果为统一格式"""

    final_docs = []

    for rrf_doc in state.get("rrf_chunks"):
        final_docs.append({
            "content": rrf_doc.get("content"),
            "title": rrf_doc.get("title"),
            "chunk_id": rrf_doc.get("chunk_id"),
            "url": None,
            "source": "milvus",
        })

    for web_doc in state.get("web_search_docs"):
        final_docs.append({
            "content": web_doc.get("snippet"),
            "title": web_doc.get("title"),
            "chunk_id": None,
            "url": web_doc.get("url"),
            "source": "web",
        })

    return final_docs


async def _step_2_rerank_merged_docs(state: QueryGraphState, merged_multi_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """使用 Reranker 模型对文档进行精排（异步版）"""
    try:
        user_query = state.get("rewritten_query")
        contents = [doc.get("content") for doc in merged_multi_docs]
        rerank_scores = await arerank_documents(user_query, contents)

        scored_docs = [{**doc, "score": score} for doc, score in zip(merged_multi_docs, rerank_scores)]
        return sorted(scored_docs, key=lambda x: x["score"], reverse=True)

    except Exception as e:
        logger.error(f"Rerank 重排序失败: {str(e)}")
        return [{**merged_multi_docs, "score": None}]

def _step_3_cliff_cutoff( ranked_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """断崖检测截断：相邻得分差距超过阈值时截断。"""
    if not ranked_docs:
        return []

    upper_bound = min(get_settings().module.rerank_max_topk, len(ranked_docs))
    lower_bound = min(get_settings().module.rerank_min_topk, upper_bound)

    # 默认值：取满硬上限（最多10条）
    cutoff_pos = upper_bound

    # 遍历范围：从min_topk-1到max_topk-2（索引从0开始），检测相邻两个文档的分数差
    # 例：min_topk=3，max_topk=10 → 遍历i=2,3,4,5,6,7,8（对应第3~9条文档，检测与下一条的差距）
    for idx in range(lower_bound - 1, upper_bound - 1):
        current_score = ranked_docs[idx].get("score")
        next_score = ranked_docs[idx + 1].get("score")

        if current_score is None or next_score is None:
            continue

        # 计算相邻文档的分数绝对差距（因已降序，gap≥0）
        abs_gap = current_score - next_score
        # 计算相对差距：绝对差距 / 当前文档分数（+1e-6避免除数为0/极小值，防止程序报错）
        # 1e-6 是 Python 中科学计数法的写法，等价于 0.000001（10 的负 6 次方，也就是百万分之一）。
        rel_gap = abs_gap / (abs(current_score) + 1e-6)

        # 触发断崖截断条件：绝对差距≥绝对阈值 OR 相对差距≥相对阈值
        # 满足任一条件，说明下一条文档相关性骤降，截断在当前位置
        if abs_gap >= get_settings().module.rerank_gap_abs or rel_gap >= get_settings().module.rerank_gap_ratio:
            # 最终取前i+1条（索引转实际数量，如i=2 → 取前3条）
            cutoff_pos = idx + 1
            logger.debug(f"断崖检测: 位置 {idx + 1}, abs_gap={abs_gap:.4f}, rel_gap={rel_gap:.4f}")
            break

    return ranked_docs[:cutoff_pos]


if __name__ == "__main__":
    mock_state = {
        "rewritten_query": "怎么测这块主板的短路问题？",
        "rrf_chunks": [
            {
                "chunk_id": "local_1",
                "title": "主板维修手册",
                "content": "主板短路通常表现为通电后风扇转一下就停，可以使用万用表的蜂鸣档测量。"
            },
            {
                "chunk_id": "local_2",
                "title": "闲聊",
                "content": "今天中午去吃猪脚饭吧，这块主板外观很漂亮。"
            },
        ],
        "web_search_docs": [
            {
                "url": "https://example.com/repair",
                "title": "短路查修指南",
                "snippet": "主板通电前先打各主供电电感对地阻值，阻值偏低就是短路。"
            },
            {
                "url": "https://example.com/news",
                "title": "科技新闻",
                "snippet": "苹果发布新款手机，A系列芯片性能提升20%。"
            },
        ],
    }
    # 执行节点的单元测试
    result = node_rerank(mock_state)
    # 输出:将返回的图状态进行json序列化
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))
