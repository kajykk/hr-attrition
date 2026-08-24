"""多租户隔离模块 - 基于 ContextVar + 中间件实现行级隔离（ADR-002）.

请求处理流程：
  1. 中间件从 JWT（Authorization: Bearer）提取 tenant_id
  2. 注入 TenantContext (ContextVar)
  3. 所有业务 SQL 强制带 WHERE tenant_id = :tenant_id
  4. 跨租户访问 → 403 Forbidden

安全约束：租户身份只能来自服务端校验过的 JWT，客户端自报的
X-Tenant-Id 头不参与上下文注入（防止伪造越权）。
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, status

# 租户上下文（每个请求独立，asyncio task 安全）
tenant_context: ContextVar[Optional["TenantContext"]] = ContextVar("tenant_context", default=None)


@dataclass
class TenantContext:
    """租户上下文，承载当前请求的租户与用户身份."""

    tenant_id: str
    user_id: str | None = None
    role: str | None = None


def set_tenant_context(ctx: TenantContext) -> None:
    """设置当前请求的租户上下文."""
    tenant_context.set(ctx)


def get_tenant_context() -> TenantContext | None:
    """获取当前请求的租户上下文."""
    return tenant_context.get()


def get_current_tenant_id() -> str:
    """获取当前 tenant_id，缺失则抛 403（防止越权）."""
    ctx = tenant_context.get()
    if ctx is None or not ctx.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="租户上下文缺失，拒绝跨租户访问",
        )
    return ctx.tenant_id


def clear_tenant_context() -> None:
    """清除租户上下文（请求结束时调用）."""
    tenant_context.set(None)


async def tenant_middleware(request, call_next):
    """FastAPI 中间件：从 Authorization Header 解析 JWT 注入 tenant_id.

    复用 DWS tenant_context 模块思路。无 Authorization 头放行（由端点 deps 二次校验）。
    """
    from jose import JWTError

    from app.core.security import decode_token

    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            payload = decode_token(token)
            if payload.get("type") == "access":
                set_tenant_context(
                    TenantContext(
                        tenant_id=str(payload.get("tenant_id", "")),
                        user_id=str(payload.get("sub", "")),
                        role=payload.get("role"),
                    )
                )
        except JWTError:
            # 无效 token 由端点依赖拦截，中间件不阻断
            pass

    # 安全说明（P0 修复）：不再信任客户端可控的 X-Tenant-Id 头注入租户上下文。
    # 租户上下文只能来自服务端校验过的 JWT；无 JWT 的服务间调用必须改用
    # 服务端凭据（API Key → 服务账号 JWT），而非自报租户头。
    try:
        response = await call_next(request)
    finally:
        clear_tenant_context()
    return response


def require_tenant_header(x_tenant_id: str | None = Header(None)) -> str:
    """依赖项：要求请求头携带 X-Tenant-Id（用于 API Key 服务间调用）.

    .. deprecated::
        仅限显式使用该依赖的端点；中间件已不再信任 X-Tenant-Id 注入租户
        上下文（防客户端伪造越权）。新代码请走 JWT。
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Tenant-Id 头部缺失",
        )
    return x_tenant_id
