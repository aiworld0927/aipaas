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
    """
    知识库查询工作流类
    封装LangGraph工作流的构建、编译、执行逻辑，支持自定义配置和多实例运行
    """

    def __init__(self):
        """初始化工作流：创建状态图、注册节点、定义路由规则"""
        # 1. 初始化LangGraph状态图
        self.workflow = StateGraph(QueryGraphState)
        # 2. 注册节点到工作流
        self._register_nodes()
        # 3. 设置入口和路由规则
        self._setup_routes()
        # 4. 编译工作流（懒加载，首次执行时编译）
        self._compiled_app: Optional[object] = None


    def _register_nodes(self):
        """注册所有节点到工作流（私有方法，统一管理节点注册）"""
        # 节点标识与实例属性名保持一致，便于维护
        self.workflow.add_node("node_item_name_confirm", node_item_name_confirm)  # 确认主体
        self.workflow.add_node("node_search_embedding", node_search_embedding)  # 向量搜索
        self.workflow.add_node("node_search_embedding_hyde", node_search_embedding_hyde)  # 假设性答案向量搜索
        self.workflow.add_node("node_web_search_mcp", node_web_search_mcp)  # 联网搜索
        self.workflow.add_node("node_rrf", node_rrf)  # 排序
        self.workflow.add_node("node_rerank", node_rerank)  # 重排
        self.workflow.add_node("node_answer_output", node_answer_output)  # 生成

    def _route_after_item_name_confirm(self,state: QueryGraphState) -> str:
        """主体名称确认后的条件路由函数"""
        if state.get("answer"):
            """
            这主要发生在 node_item_name_confirm 节点无法直接确定唯一的商品型号，从而需要“反问用户”或“拒绝回答”的场景。
            具体来说，有以下两种情况会导致 state 中直接出现 answer ，从而跳过后续的检索流程，直接输出：
            1. 多选一（反问用户） ：
            - 场景 ：用户问得太模糊（比如“华为P60”），系统发现数据库里有“华为P60 128G”和“华为P60 Art”两个型号，不足以直接确认。
            - 处理 ：节点会生成一条反问句作为 answer ，例如：“您是想问以下哪个产品：华为P60 128G、华为P60 Art？请明确一下型号。”
            - 结果 ：此时不需要去检索文档了，直接把这句话发给用户让他选。

            2. 查无此人（拒绝回答） ：
            - 场景 ：用户问了一个系统里压根没有的商品（比如“小米15”，库里只有华为的数据）。
            - 处理 ：节点会生成一条拒绝句作为 answer ，例如：“抱歉，未找到相关产品，请提供准确型号以便我为您查询。”
            - 结果 ：同样不需要后续检索，直接结束流程。
            """
            return "node_answer_output"

        # 否则继续搜索流程
        return ["node_search_embedding", "node_search_embedding_hyde", "node_web_search_mcp"]

    def _setup_routes(self):
        """设置工作流路由规则"""
        # 1、设置入口节点
        self.workflow.set_entry_point("node_item_name_confirm")

        # 2、注册条件路由边
        self.workflow.add_conditional_edges(
            source="node_item_name_confirm",
            path=self._route_after_item_name_confirm,
            path_map={
                "node_answer_output": "node_answer_output",
                "node_search_embedding": "node_search_embedding",
                "node_search_embedding_hyde": "node_search_embedding_hyde",
                "node_web_search_mcp": "node_web_search_mcp"
            }
        )

        # 3. 多路搜索结果合并
        self.workflow.add_edge("node_search_embedding", "node_rrf")
        self.workflow.add_edge("node_search_embedding_hyde", "node_rrf")
        self.workflow.add_edge("node_web_search_mcp", "node_rrf")

        # 4. 排序 -> 重排 -> 生成 -> 结束
        self.workflow.add_edge("node_rrf", "node_rerank")
        self.workflow.add_edge("node_rerank", "node_answer_output")
        self.workflow.add_edge("node_answer_output", END)

    def compile(self):
        """编译工作流（公开方法，支持手动触发编译）"""
        if not self._compiled_app:
            self._compiled_app = self.workflow.compile()
        return self._compiled_app

    def run(self, initial_state: QueryGraphState, stream: bool = False) -> QueryGraphState:
        """
        统一执行入口，支持切换invoke/stream
        :param initial_state:  初始状态对象
        :param stream: 是否是流式输出
        :return: 执行完成后的状态对象
        """
        """"""
        if not self._compiled_app:
            self.compile()

        # self.print_ascii()

        if stream:
            return self._compiled_app.stream(initial_state)
        else:
            return self._compiled_app.invoke(initial_state)

    def print_ascii(self):
        self._compiled_app.get_graph().print_ascii()


# ===================== 用法示例 =====================
if __name__ == "__main__":

    # 定义初始状态
    init_state = {
        "session_id": "222",
        "original_query": "华为擎云B530如何使用？"
    }

    workflow = KBQueryWorkflowV2()
    # final_state = workflow.run(init_state)
    # logger.info(final_state)
    # 流式输出
    for chunk in workflow.run(create_default_query_state(**init_state), stream=True):
        logger.warning(chunk)

    # 打印编译后的图结构
    # logger.info(workflow.compile().get_graph().draw_ascii())
