"""slowapi 限流集成（P1-6：登录防爆破）.

登录端点使用 settings.RATE_LIMIT_LOGIN（默认 5/minute），按客户端真实 IP 计数。

IP 识别（P0-2 修复 + 绕过加固）：
  X-Forwarded-For 是客户端可伪造的头，最左侧段完全不可信。nginx 注入
  `$proxy_add_x_forwarded_for` 时会把"直连对端真实 IP"追加到**最右侧**。
  因此本模块从右往左取第 N 段（N = TRUSTED_PROXY_COUNT，默认 1）作为限流
  粒度 IP：每经过一层可信代理，XFF 右侧多一个该代理写入的真实地址。
  - N=1（单层 nginx）：取最右段 = nginx 看到的真实客户端 IP
  - N=2（CDN → nginx）：取倒数第二段
  - XFF 段数 < N 时说明存在伪造/异常，整体不信任，回退直连地址
  配套约束：api 容器端口仅绑定 127.0.0.1（compose），必须经 nginx 入口访问，
  防止攻击者绕过代理直连后端伪造成任意 IP 的限流键。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.core.config import settings


def _client_ip(request: Request) -> str:
    """从请求提取限流粒度 IP.

    - 有 X-Forwarded-For 时从右往左取第 TRUSTED_PROXY_COUNT 个可信段
      （最左侧为客户端可伪造值，绝不采用）
    - 无代理头 / 可信段不足时回退 TCP 直连地址
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        n = max(1, settings.TRUSTED_PROXY_COUNT)
        idx = len(parts) - n
        if idx >= 0 and parts[idx]:
            return parts[idx]
        # 段数少于可信代理层数 → 头部可疑（伪造/截断），整体弃用
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
