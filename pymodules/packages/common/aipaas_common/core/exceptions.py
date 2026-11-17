"""异常类型约定。

``BusinessError`` 表示可预期的业务失败（例如上游 MinerU 判定文件无法解析）。
日志只输出原因与上下文，不打印堆栈；其他未预期异常仍保留完整堆栈便于排查。
"""
from __future__ import annotations


class BusinessError(RuntimeError):
    """可预期的业务失败：对用户/运维有明确处理建议，无需堆栈。"""
