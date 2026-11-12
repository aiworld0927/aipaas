import asyncio
from typing import Optional

from aipaas_common.conf.settings import load_settings
from langgraph.constants import END
from langgraph.graph import StateGraph

# 配置引导（必须在导入业务节点前执行；幂等，已加载则返回缓存）
load_settings("query_worker")

from aipaas_common.core.log import logger
from query_worker.graph.nodes.a_node_item_name_confirm import node_item_name_confirm
from query_worker.graph.nodes.b_node_search_embedding import node_search_embedding
from query_worker.graph.nodes.c_node_search_embedding_hyde import node_search_embedding_hyde
from query_worker.graph.nodes.d_node_web_search_mcp import node_web_search_mcp
from query_worker.graph.nodes.e_node_rrf import node_rrf
from query_worker.graph.nodes.f_node_rerank import node_rerank
from query_worker.graph.nodes.g_node_answer_output import node_answer_output
from query_worker.graph.state import QueryGraphState, create_default_query_state


class KBQueryWorkflowV2:
    """知识库查询工作流类。

    所有节点均为 async def，LangGraph v1.2+ compile 后 graph 同时提供
    同步 (.invoke/.stream) 和异步 (.ainvoke/.astream) API；本类对外暴露：
    - run() 同步入口（内部走 asyncio.run，兼容 CLI / 测试）
    - arun() 异步入口（FastAPI / 服务化链路首选）
    """

    def __init__(self):
        self.workflow = StateGraph(QueryGraphState)
        self._register_nodes()
        self._setup_routes()
        self._compiled_app: Optional[object] = None

    def _register_nodes(self):
        self.workflow.add_node("node_item_name_confirm", node_item_name_confirm)
        self.workflow.add_node("node_search_embedding", node_search_embedding)
        self.workflow.add_node("node_search_embedding_hyde", node_search_embedding_hyde)
        self.workflow.add_node("node_web_search_mcp", node_web_search_mcp)
        self.workflow.add_node("node_rrf", node_rrf)
        self.workflow.add_node("node_rerank", node_rerank)
        self.workflow.add_node("node_answer_output", node_answer_output)

    def _route_after_item_name_confirm(self, state: QueryGraphState) -> str:
        if state.get("answer"):
            return "node_answer_output"
        return ["node_search_embedding", "node_search_embedding_hyde", "node_web_search_mcp"]

    def _setup_routes(self):
        self.workflow.set_entry_point("node_item_name_confirm")
        self.workflow.add_conditional_edges(
            source="node_item_name_confirm",
            path=self._route_after_item_name_confirm,
            path_map={
                "node_answer_output": "node_answer_output",
                "node_search_embedding": "node_search_embedding",
                "node_search_embedding_hyde": "node_search_embedding_hyde",
                "node_web_search_mcp": "node_web_search_mcp",
            },
        )
        self.workflow.add_edge("node_search_embedding", "node_rrf")
        self.workflow.add_edge("node_search_embedding_hyde", "node_rrf")
        self.workflow.add_edge("node_web_search_mcp", "node_rrf")
        self.workflow.add_edge("node_rrf", "node_rerank")
        self.workflow.add_edge("node_rerank", "node_answer_output")
        self.workflow.add_edge("node_answer_output", END)

    def compile(self):
        if not self._compiled_app:
            self._compiled_app = self.workflow.compile()
        return self._compiled_app

    # ---- 同步入口：给 CLI / 测试 / 没有 event loop 的场景 ----
    def run(self, initial_state: QueryGraphState, stream: bool = False):
        self.compile()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 当前线程没有 event loop → 自己起一个
            return asyncio.run(self._arun(initial_state, stream))
        else:
            # 已经在 loop 里（FastAPI 服务链路），不能再起 loop，用 create_task + 临时事件循环阻塞
            # 这种情况服务侧应直接用 arun；这里兜底给旧调用方：asyncio.ensure_future
            future = asyncio.ensure_future(self._arun(initial_state, stream))
            # asyncio.run_coroutine_threadsafe 也可选；这里用 loop.run_until_complete
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(self._arun(initial_state, stream))).result()

    # ---- 异步入口：服务化链路首选 ----
    async def arun(self, initial_state: QueryGraphState, stream: bool = False):
        return await self._arun(initial_state, stream)

    async def _arun(self, initial_state: QueryGraphState, stream: bool):
        self.compile()
        app = self._compiled_app
        if stream:
            # LangGraph astream 返回 AsyncIterator
            result = []
            async for chunk in app.astream(initial_state):
                logger.debug("graph chunk:", chunk)
                result.append(chunk)
            return result
        else:
            return await app.ainvoke(initial_state)

    def print_ascii(self):
        self._compiled_app.get_graph().print_ascii()


if __name__ == "__main__":
    init_state = {"session_id": "222", "original_query": "华为擎云B530如何使用？"}
    workflow = KBQueryWorkflowV2()
    # 流式：asyncio.run 会自动起 loop（CLI 下没 loop）
    result = asyncio.run(workflow.arun(create_default_query_state(**init_state), stream=True))
    for chunk in result:
        logger.warning(chunk)
