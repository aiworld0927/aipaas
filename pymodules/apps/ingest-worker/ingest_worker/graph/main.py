import json

from langgraph.constants import END
from langgraph.graph import StateGraph

from ingest_worker.graph.nodes.a_node_entry import node_entry
from ingest_worker.graph.nodes.b_node_pdf_to_md import node_pdf_to_md
from ingest_worker.graph.nodes.c_node_md_img import node_md_img
from ingest_worker.graph.nodes.d_node_document_split import node_document_split
from ingest_worker.graph.nodes.e_node_item_name_recognition import node_item_name_recognition
from ingest_worker.graph.nodes.f_node_bge_embedding import node_bge_embedding
from ingest_worker.graph.nodes.g_node_import_milvus import node_import_milvus
from ingest_worker.graph.state import ImportGraphState, get_default_ingest_state

class KBImportWorkflow:
    """知识库导入工作流"""

    def __init__(self,config=None):
        """
        初始化工作流
        """
        self.__compiled_graph = None

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
        graph.add_node("node_import_milvus",node_import_milvus)

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

    def run(self, state: ImportGraphState, stream: bool = False):
        """
        统一执行入口，支持切换invoke/stream
        :param state: 初始状态
        :param stream: 是否流式输出
        :return: 执行结果
        """
        if stream:
            # return self.graph.stream(state, stream_mode="values")
            return self.graph.stream(state)
        else:
            return self.graph.invoke(state)

    def print_ascii(self):
        self.graph.get_graph().print_ascii()


if __name__ == "__main__":
    from aipaas_common.core.log import logger
    from aipaas_common.utils.path_util import PROJECT_ROOT
    from ingest_worker.graph.state import get_default_ingest_state
    # 定义初始状态
    init_state = get_default_ingest_state(**{"import_file_path": str(PROJECT_ROOT / "data" / "inputs" / "H3CLA2608室内无线网关用户手册.pdf")})

    workflow = KBImportWorkflow()

    # 方式1：实例化后使用（推荐方式，可复用）
    # for event in workflow.run(create_default_state(**init_state), stream=True):
    #     logger.info(f"state: {event}")

    # 方式2：非流式执行
    final_state = workflow.run(init_state, stream=False)
    logger.info(json.dumps(final_state, ensure_ascii=False, indent=4))

    # 打印编译后的图结构
    # workflow.print_ascii()
