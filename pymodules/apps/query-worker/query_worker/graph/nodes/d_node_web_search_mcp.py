# processor/query_processor/nodes/node_web_search_mcp.py
import asyncio

from agents.mcp import MCPServerStreamableHttp

from aipaas_common.conf.bailian_mcp_config import mcp_config
from aipaas_common.log_decorator import node_log
from query_worker.graph.state import QueryGraphState


@node_log("node_web_search_mcp")
def node_web_search_mcp( state: QueryGraphState) -> QueryGraphState:
    """
    节点功能，调用外部搜索引擎补充信息
    """

    query = state.get("rewritten_query", "")
    docs = []
    # 如果没有查询内容，直接返回
    if query:
        result = asyncio.run(_mcp_call(query))
        if result:
            pages = json.loads(result.content[0].text).get("pages") or []
            # 统一输出结构化结果，供后续 rerank/引用使用
            # 每条：{title, url, snippet}

            for item in pages:
                snippet = (item.get("snippet") or "").strip()
                url = (item.get("url") or "").strip()
                title = (item.get("title") or "").strip()
                if not snippet:
                    continue
                docs.append({"title": title, "url": url, "snippet": snippet})

            logger.info("MCP 搜索结果:", docs)

    if docs:
        return {"web_search_docs": docs}
    return {}


async def _mcp_call( query):

    search_mcp = MCPServerStreamableHttp(
        name="search_mcp",
        params={
            "url": mcp_config.mcp_base_url,
            "headers": {"Authorization": f"Bearer {mcp_config.api_key}"},
            "timeout": 30.0,
            "sse_read_timeout": 300.0,
        },
        cache_tools_list=True,
        max_retry_attempts=3,
        client_session_timeout_seconds=60.0,  # MCP 客户端会话超时
    )
    async with search_mcp:  # 自动 connect 和 cleanup，比手动 connect/cleanup 更安全
        result = await search_mcp.call_tool(
            tool_name="bailian_web_search",
            arguments={"query": query, "count": 5},
        )
        return result


if __name__ == "__main__":
    import json
    from aipaas_common.core.log import logger
    from query_worker.graph.state import create_default_query_state

    # 初始化图状态
    init_state = {
        "original_query": "H3CLA2608室内无线网关怎么使用？",
        "rewritten_query": "H3CLA2608室内无线网关怎么使用？",
        "item_names": ["H3CLA2608-GM/GU室内无线网关"],
    }
    init_state = create_default_query_state(**init_state)
    # 执行节点的单元测试
    result = node_web_search_mcp(init_state)
    # 输出:将返回的图状态进行json序列化
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))