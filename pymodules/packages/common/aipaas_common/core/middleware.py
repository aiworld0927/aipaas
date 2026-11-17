"""通用 HTTP 中间件。

当前只包含请求 ID 注入：每个请求生成唯一 ID 写入 ``request_id_ctx_var``，
日志与下游调用可通过上下文变量拿到同一条请求的标识。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request

from aipaas_common.core.context import request_id_ctx_var


def install_request_id_middleware(app: FastAPI, prefix: str) -> None:
    """为应用安装请求 ID 中间件。

    :param app: FastAPI 实例
    :param prefix: 请求 ID 前缀（如 ``ingest`` / ``query``），便于跨服务检索日志
    """

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id_ctx_var.set(f"{prefix}_{uuid.uuid4()}")
        return await call_next(request)
