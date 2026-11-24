import asyncio
import json
from typing import Optional

from langgraph.constants import END
from langgraph.graph import StateGraph

from aipaas_common.conf.settings import load_settings

# 配置引导：命令行/脚本入口独立运行时，先加载配置再导入节点
load_settings("app_ingest_worker")

from app_ingest_worker.graph.nodes.a_node_entry import node_entry
from app_ingest_worker.graph.nodes.b_node_pdf_to_md import node_pdf_to_md
from app_ingest_worker.graph.nodes.c_node_md_img import node_md_img
from app_ingest_worker.graph.nodes.d_node_document_split import node_document_split
from app_ingest_worker.graph.nodes.e_node_item_name_recognition import node_item_name_recognition
from app_ingest_worker.graph.nodes.f_node_bge_embedding import node_bge_embedding
from app_ingest_worker.graph.nodes.g_node_import_milvus import node_import_milvus
from app_ingest_worker.graph.state import ImportGraphState, create_default_ingest_state

class KBImportWorkflow:
    """知识库导入工作流。

    所有节点均为 async def，LangGraph v1.2+ compile 后 graph 同时提供
    同步 (.invoke/.stream) 和异步 (.ainvoke/.astream) API；本类对外暴露：
    - run() 同步入口（内部走 asyncio.run，兼容 CLI / 测试）
    - arun() 异步入口（FastAPI / 服务化链路首选）
    """

    def __init__(self, config=None):
        """
        初始化工作流
        """
        self.__compiled_graph: Optional[object] = None

    @property  # 将方法封装为属性
    def graph(self):
        """懒加载：只在第一次使用时编译图"""
        if self.__compiled_graph is None:
            self.__compiled_graph = self.build_graph()
        return self.__compiled_graph

    @staticmethod
    def route_after_entry(state: ImportGraphState) -> str:
        """
        入口节点后的条件路由函数
        :param state: 当前状态
        :return: 下一个节点名称
        """
        if state.get("is_pdf_read_enabled"):
            return "node_pdf_to_md"
        elif state.get("is_md_read_enabled"):
            return "node_md_img"
        else:
            return END

    def build_graph(self):
        """
        创建图结构
        :return: 编译后的图
        """
        # 1. 初始化LangGraph状态图
        graph = StateGraph(ImportGraphState)

        # 2. 注册节点到工作流
        graph.add_node("node_entry", node_entry)
        graph.add_node("node_pdf_to_md", node_pdf_to_md)
        graph.add_node("node_md_img", node_md_img)
        graph.add_node("node_document_split", node_document_split)
        graph.add_node("node_item_name_recognition", node_item_name_recognition)
        graph.add_node("node_bge_embedding", node_bge_embedding)
        graph.add_node("node_import_milvus", node_import_milvus)

        # 3. 设置入口节点
        graph.set_entry_point("node_entry")

        # 4. 注册条件边
        graph.add_conditional_edges(
            "node_entry",
            self.route_after_entry,
            {
                # key：路由函数的返回值，value:节点的名字
                "node_md_img": "node_md_img",
                "node_pdf_to_md": "node_pdf_to_md",
                END: END
            }
        )

        # 5. 注册顺序边
        graph.add_edge("node_pdf_to_md", "node_md_img")
        graph.add_edge("node_md_img", "node_document_split")
        graph.add_edge("node_document_split", "node_item_name_recognition")
        graph.add_edge("node_item_name_recognition", "node_bge_embedding")
        graph.add_edge("node_bge_embedding", "node_import_milvus")
        graph.add_edge("node_import_milvus", END)

        # 6. 编译工作流
        return graph.compile()

    # ---- 同步入口：给 CLI / 测试 / 没有 event loop 的场景 ----
    def run(self, state: ImportGraphState, stream: bool = False):
        """
        同步执行入口，支持切换 invoke/stream
        :param state: 初始状态
        :param stream: 是否流式输出
        :return: 执行结果
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 当前线程没有 event loop → 自己起一个
            return asyncio.run(self._arun(state, stream))
        else:
            # 已经在 loop 里（FastAPI 服务链路），不能再起 loop
            # 旧调用方兜底：asyncio.run_coroutine_threadsafe
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(self._arun(state, stream))).result()

    # ---- 异步入口：服务化链路首选 ----
    async def arun(self, state: ImportGraphState, stream: bool = False):
        """
        异步执行入口，FastAPI/服务化链路首选
        :param state: 初始状态
        :param stream: 是否流式输出
        :return: 执行结果（stream=False 返回最终 state；stream=True 返回 event 列表）
        """
        return await self._arun(state, stream)

    async def _arun(self, state: ImportGraphState, stream: bool):
        app = self.graph
        if stream:
            result = []
            async for event in app.astream(state):
                result.append(event)
            return result
        else:
            return await app.ainvoke(state)

    def print_ascii(self):
        self.graph.get_graph().print_ascii()


if __name__ == "__main__":
    from aipaas_common.core.log import logger
    from aipaas_common.utils.path_util import PROJECT_ROOT
    from app_ingest_worker.graph.state import create_default_ingest_state
    # 定义初始状态
    init_state = create_default_ingest_state(**{"import_file_path": str(PROJECT_ROOT / "data" / "inputs" / "H3C LA2608室内无线网关 用户手册-6W100-整本手册.pdf")})

    workflow = KBImportWorkflow()

    # 方式1：实例化后使用（推荐方式，可复用）
    # for event in workflow.run(create_default_state(**init_state), stream=True):
    #     logger.info(f"state: {event}")

    # 方式2：非流式执行
    final_state = workflow.run(init_state, stream=False)
    logger.info(json.dumps(final_state, ensure_ascii=False, indent=4))

    # 打印编译后的图结构
    # workflow.print_ascii()
