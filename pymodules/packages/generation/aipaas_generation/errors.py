"""生成链路错误类型：区分参数错误、入口拦截与模型不可用。

错误码与 ``aipaas-openapi-v1.yaml`` 的 ``SYS|BIZ|PARAM`` 前缀约定保持一致，
供上层直接映射为 HTTP 状态码，避免把供应商原始异常信息透出。
"""

from __future__ import annotations

from typing import Any


class GenerationError(Exception):
    """生成链路基础异常。"""

    code = "GENERATION_ERROR"


class InvalidInput(GenerationError):
    """请求参数不合法（空查询、超长、互斥字段等）。"""

    code = "PARAM_INVALID"


class InputBlocked(GenerationError):
    """入口校验拦截：触发可配置规则，调用方应拒绝请求并保留审计。"""

    code = "INPUT_BLOCKED"

    def __init__(
        self,
        message: str,
        *,
        rule_id: str,
        category: str,
        reason: str,
    ) -> None:
        super().__init__(message)
        self.rule_id = rule_id
        self.category = category
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "category": self.category,
            "reason": self.reason,
            "retryable": False,
        }


class ModelUnavailable(GenerationError):
    """全部供应商均不可用：携带每次尝试的脱敏结果，便于前端展示降级提示。"""

    code = "MODEL_UNAVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        attempts: list[dict[str, Any]] | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.attempts = list(attempts or [])
        self.retryable = retryable


class TemplateNotFound(GenerationError):
    """Prompt 模板或版本不存在。"""

    code = "TEMPLATE_NOT_FOUND"
