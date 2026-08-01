"""请求 ID 中间件（P2-11）：生成/透传 X-Request-ID 并注入日志.

- 优先沿用客户端 X-Request-ID（便于全链路追踪），否则生成 uuid4
- 响应头返回 X-Request-ID
- 通过 logging.Filter 将 request_id 附加到当前线程日志 record
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdFilter(logging.Filter):
    """为日志 record 附加 request_id 字段."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


def get_request_id() -> str:
    """当前请求 ID（无请求上下文时返回 '-'）."""
    return _request_id_ctx.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """生成/透传请求 ID 并设置 ContextVar."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        token = _request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
