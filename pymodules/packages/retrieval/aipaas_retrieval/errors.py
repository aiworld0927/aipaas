"""检索层错误类型：区分参数错误、越权与后端降级。"""

from __future__ import annotations


class RetrievalError(Exception):
    """检索层基础异常。"""

    code = "RETRIEVAL_ERROR"


class InvalidRequest(RetrievalError):
    """请求参数不合法（如 kbId 与 kbIds 同时传入）。"""

    code = "PARAM_INVALID"


class PermissionDenied(RetrievalError):
    """无检索权限或无权访问目标知识库。"""

    code = "FORBIDDEN"

    def __init__(self, message: str, *, reason: str, kb_id: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.kb_id = kb_id
