"""slowapi 限流集成（P1-6：登录防爆破）.

登录端点使用 settings.RATE_LIMIT_LOGIN（默认 5/minute），基于客户端 IP 计数。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(key_func=get_remote_address)


def init_limiter(app: FastAPI) -> None:
    """将 limiter 挂载到 app.state 并注册 429 异常处理."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 统一响应（含 Retry-After）."""
    detail = exc.detail
    if isinstance(detail, list):
        detail = "请求过于频繁，请稍后再试"
    headers = dict(exc.headers) if exc.headers else {}
    headers.setdefault("Retry-After", "60")
    return JSONResponse(
        status_code=429,
        content={"detail": detail or "请求过于频繁，请稍后再试"},
        headers=headers,
    )


def login_limit():
    """登录限流装饰器（按 IP，默认 5/minute，可通过 RATE_LIMIT_LOGIN 配置）."""
    return limiter.limit(settings.RATE_LIMIT_LOGIN)
