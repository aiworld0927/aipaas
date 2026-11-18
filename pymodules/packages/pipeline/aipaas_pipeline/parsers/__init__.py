"""内置解析器集合；新增格式只需实现协议并在 ``registry._builtin_parsers`` 注册。"""

from aipaas_pipeline.parsers.base import DocumentParser

__all__ = ["DocumentParser"]
