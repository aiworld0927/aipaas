import asyncio

from agents.mcp import MCPServerStreamableHttp
from mcp.types import TextContent

from aipaas_common.conf.bailian_mcp_config import mcp_config


async def call_web_search_mcp(rewritten_query:str,max_retries:int=3):
    """
    调用MCP网络搜索服务
    :param rewritten_query:
    :return:
    """
    mcp_result = ""
    for i in range(max_retries):
        # 1、链接mcpserver服务
        mcp_server = MCPServerStreamableHttp(
            name="search_mcp",
            params={
                "url":"https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
                "headers":{"Authorization":f"Bearer {mcp_config.bailian_api_key}"},
                "timeout":300,
                "sse_read_timeout":300,
            },
        )

        try:
            # 2、进行mcp 链接
            await mcp_server.connect()
            # 3、调用工具
            tool_list = await mcp_server.list_tools()
            print(f"tool_list:{tool_list}")
            mcp_result = await mcp_server.call_tool(
                tool_name="bailian_web_search",
                arguments={
                    "query":rewritten_query,
                    "count":2
                }
            )
            break
        except Exception as e:
            if i == max_retries - 1:
                raise
            wait_time = 2 ** i  # 指数退避：1, 2, 4 秒
            print(f"请求失败，{wait_time}秒后重试...")
            await asyncio.sleep(wait_time)
        finally:
            # 释放本次资源链接
            await mcp_server.cleanup()
    return mcp_result


async def test():
    async with MCPServerStreamableHttp(
            name="search_mcp",
            params={
                "url":"https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
                "headers":{"Authorization":f"Bearer {mcp_config.bailian_api_key}"},
                "timeout":300,
                "sse_read_timeout":300,
            },
    ) as mcp_server:
        print(f"mcp_server::::{mcp_server}")
        result = await mcp_server.call_tool(
            "bailian_web_search",
            {"query": "测试数据", "count": 2},
        )
        # print(result)
        if result.isError:
            raise RuntimeError(f"MCP工具调用失败：{result.content}")
        return "\n".join(content.text for content in result.content if isinstance(content, TextContent))

if __name__ == '__main__':
    result = asyncio.run(test())
    print(result)
    # result = asyncio.run(call_web_search_mcp_new2("测试数据"))
    # result = asyncio.run(call_web_search_mcp_new("测试数据"))
    # result = asyncio.run(call_web_search_mcp("测试数据"))
    # print(result)