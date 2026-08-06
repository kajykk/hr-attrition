"""slowapi 限流集成（P1-6：登录防爆破）.

登录端点使用 settings.RATE_LIMIT_LOGIN（默认 5/minute），按客户端真实 IP 计数。

IP 识别（P0-2 修复）：
  优先取 X-Forwarded-For 中**最左侧**的原始客户端地址（nginx 已注入
  `$proxy_add_x_forwarded_for`），无该头时回退 request.client.host。
  注意：仅信任 nginx 反代注入的头，生产环境应确保 8000 端口不直接暴露（由
  nginx 统一入口），避免伪源头绕过限流。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.core.config import settings


def _client_ip(request: Request) -> str:
    """从请求提取限流粒度 IP.

    - 优先 X-Forwarded-For 最左侧（原始客户端）
    - 无代理头则回退直连地址
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = request.client
    return client.host if client else "unknown"


limiter = Limiter(key_func=_client_ip)


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
