import asyncio

from agents.mcp import MCPServerStreamableHttp
from regex import _main

from conf.bailian_mcp_config import mcp_config


async def _mcp_call(query):
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
        client_session_timeout_seconds=60.0,
    )

    async with search_mcp:  # 自动 connect 和 cleanup，比手动 connect/cleanup 更安全
        result = await search_mcp.call_tool(
            tool_name="bailian_web_search",
            arguments={"query": query, "count": 5},
        )
        return result

if __name__ == '__main__':
    result = asyncio.run(_mcp_call("碳经济"))
    print(result)


"""
修复办法：
uv add "mcp<2" "openai-agents==0.23.1"

curl -v -X POST "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp" \
  -H "Authorization: Bearer sk-cf1ad4e15ada4e839d4535f6e1b519d5" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"server/discover","params":{},"id":1}'
"""