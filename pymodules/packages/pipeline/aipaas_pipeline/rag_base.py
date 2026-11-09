"""
导入流程节点基类

定义统一的节点接口规范，提供通用功能
"""

from abc import ABC, abstractmethod
from typing import TypeVar, Optional

from aipaas_common.core.log import logger

from aipaas_common.utils.task_utils import add_done_task, add_running_task

T = TypeVar("T")  # 泛型状态类型

"""
1、抽象的类（ABC），子类需要实现这个类
2、抽象的方法（@abstractmethod process），子类需要实现这个方法
3、name属性，子类必须覆盖这个属性
4、__init__() 方法中初始化了日志记录器，日志记录器的名字是 self所在类的名字
5、__init__() 方法中初始化了全局配置，获取全局单例配置对象
6、__call__() 方法的调用时机  
    (1)对象 = 类名() 
    (2)结果 = 对象()     圆括号调用了__call__()方法
7、self.process()， 当前这句话是从哪调用过来的，process()就定义在哪个位置
8、未来可以在父类的process()中实现任务追踪
9、封装了统一日志处理，使用logging、colorlog
    注意：使用日志记录器记录日志时一定要先激活日志setup_logging(logging.INFO)，并指定日志级别
"""

class ImportProcessError(Exception):
    """流程基类异常（公共库版本）"""
    def __init__( message='', node_name='', cause=None):
        self.node_name = node_name
        self.cause = cause
        super().__init__(message)

class BaseNode(ABC):
    """
    导入流程节点基类

    所有节点类都应继承此基类，实现 process 方法。
    基类提供统一的日志、任务追踪和错误处理。

    使用示例:
        class MyNode(BaseNode):
            name = "my_node"

            def process( state):
                # 实现具体逻辑
                return state

        # 作为 LangGraph 节点使用
        node = MyNode()
        workflow.add_node("my_node", node)
    """

    name: str = "base_node"  # 节点名称，子类应覆盖

    def __init__(self):
        """
        初始化节点
        Args:
            config: 配置对象，默认使用全局配置
        """
        # self.config = config or get_config()
        self.logger = logger

    def __call__( state: T) -> T:
        """
        节点执行入口
        LangGraph 调用节点时会调用此方法。
        提供统一的日志输出、任务追踪和异常处理。
        Args:
            state: 图状态字典
        Returns:
            更新后的状态字典
        Raises:
            ImportProcessError: 节点执行失败时抛出
        """
        try:
            task_id = state.get("task_id") or state.get("session_id")
            is_stream = state.get("is_stream")
            # 1. 开始：记录节点运行状态
            self.logger.info(f"--- {self.name} 开始 ---")
            add_running_task(task_id, self.name,is_stream=is_stream)

            # 2. 执行节点
            result = self.process(state)

            # 3. 结束：记录节点完成状态
            add_done_task(task_id, self.name,is_stream=is_stream)
            self.logger.info(f"--- {self.name} 完成 ---")

            return result
        except Exception as e:
            self.logger.error(f"{self.name} 执行失败: {e}")
            raise ImportProcessError(message=str(e), node_name=self.name, cause=e)

    @abstractmethod
    def process( state: T) -> T:
        """
        节点核心处理逻辑
        子类必须实现此方法。
        Args:
            state: 图状态字典
        Returns:
            更新后的状态字典
        """
        pass

    def log_step( step_name: str, message: str = ""):
        """
        记录步骤日志
        Args:
            step_name: 步骤名称
            message: 附加信息
        """
        log_msg = f"[{step_name}]"
        if message:
            log_msg += f" {message}"
        self.logger.info(log_msg)
