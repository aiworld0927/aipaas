import asyncio
import json

import httpx2
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from aipaas_common.conf.bailian_mcp_config import mcp_config


class _LegacyFallbackTransport(httpx2.AsyncBaseTransport):
    """
    兼容旧版 MCP 服务端的传输层
    MCP SDK v2 连接时会先发送 server/discover（协议 2026-07-28）探测服务端版本，
    若返回 JSON-RPC 错误则自动回退到 initialize 握手。但百炼 MCP 仅支持 2024-11-05，
    对 server/discover 直接返回空响应体的 HTTP 500，SDK 视为网络故障而不回退，导致连接失败。
    这里将该 500 转换为 JSON-RPC "Method not found" 错误，触发 SDK 的标准回退逻辑。
    """

    def __init__(self):
        self._transport = httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        response = await self._transport.handle_async_request(request)
        if response.status_code < 500:
            return response
        try:
            payload = json.loads(await request.aread())
        except (TypeError, ValueError):
            return response
        if not isinstance(payload, dict) or payload.get("method") != "server/discover":
            return response
        await response.aclose()
        error_body = {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32601, "message": "Method not found: server/discover"},
        }
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(error_body).encode("utf-8"),
            request=request,
        )

    async def aclose(self):
        await self._transport.aclose()


def _legacy_compatible_client_factory(headers=None, timeout=None, auth=None):
    """创建带旧版协议回退能力的 httpx2 客户端（与 agents 默认工厂参数保持一致）"""
    kwargs = {"follow_redirects": False, "transport": _LegacyFallbackTransport()}
    if headers is not None:
        kwargs["headers"] = headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if auth is not None:
        kwargs["auth"] = auth
    return httpx2.AsyncClient(**kwargs)


async def call_web_search_mcp_new2(rewritten_query: str, max_retries: int = 3) -> str:
    """
    按 OpenAI Agents SDK 规范使用 MCPServerStreamableHttp 调用百炼网络搜索服务
    - async with：自动 connect() / cleanup()，无需手动释放连接
    - cache_tools_list：缓存工具列表，减少 tools/list 往返
    - max_retry_attempts / retry_backoff_seconds_base：SDK 内置 list_tools()/call_tool() 指数退避重试
    - tool_filter：仅公开 bailian_web_search 工具（后续挂到 Agent 的 mcp_servers 时同样生效）
    - httpx_client_factory：兼容百炼旧版协议（server/discover 返回 500 时回退 initialize 握手）
    文档：https://openai.github.io/openai-agents-python/zh/mcp/
    :param rewritten_query: 搜索语句
    :param max_retries: call_tool 失败后的最大重试次数
    :return: 搜索结果文本（多个 TextContent 以换行拼接）
    """
    async with MCPServerStreamableHttp(
        name="search_mcp",
        params={
            "url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp",
            "headers": {"Authorization": f"Bearer {mcp_config.bailian_api_key}"},
            "timeout": 300,
            "sse_read_timeout": 300,
            "httpx_client_factory": _legacy_compatible_client_factory,
        },
        cache_tools_list=True,
        client_session_timeout_seconds=300,
        max_retry_attempts=max_retries,
        retry_backoff_seconds_base=1.0,
        tool_filter=create_static_tool_filter(allowed_tool_names=["bailian_web_search"]),
    ) as server:
        print("连接成功！")
        result = await server.call_tool(
            "bailian_web_search",
            {"query": rewritten_query, "count": 2},
        )
        if result.isError:
            raise RuntimeError(f"MCP工具调用失败：{result.content}")
        return "\n".join(
            content.text for content in result.content if isinstance(content, TextContent)
        )


if __name__ == '__main__':
    result = asyncio.run(call_web_search_mcp_new2("测试数据"))
    print(result)