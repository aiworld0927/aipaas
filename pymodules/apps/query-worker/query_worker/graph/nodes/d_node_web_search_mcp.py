# processor/query_processor/nodes/node_web_search_mcp.py
import asyncio
import json

from aipaas_common.core.log import logger
from aipaas_common.log_decorator import node_log
from aipaas_mcp_agents.mcp_client import call_web_search_mcp
from query_worker.graph.state import QueryGraphState


@node_log("node_web_search_mcp")
async def node_web_search_mcp(state: QueryGraphState) -> QueryGraphState:
    """调用外部 MCP 搜索引擎补充信息（异步版）。

    注意：call_web_search_mcp 本身就是 async def，历史实现用 asyncio.run() 起
    新事件循环是反模式，在已有事件循环的上下文中会炸；这里直接 await。
    """

    query = state.get("rewritten_query", "")
    docs = []
    if query:
        result = await call_web_search_mcp(query)
        if result:
            pages = json.loads(result.content[0].text).get("pages") or []
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


if __name__ == "__main__":
    from query_worker.graph.state import create_default_query_state

    init_state = create_default_query_state(
        original_query="H3CLA2608室内无线网关怎么使用？",
        rewritten_query="H3CLA2608室内无线网关怎么使用？",
        item_names=["H3CLA2608室内无线网关"],
    )
    result = asyncio.run(node_web_search_mcp(init_state))
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))