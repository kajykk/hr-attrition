"""多租户隔离模块 - 基于 ContextVar + 中间件实现行级隔离（ADR-002）.

请求处理流程：
  1. 中间件从 JWT/Header 提取 tenant_id
  2. 注入 TenantContext (ContextVar)
  3. 所有业务 SQL 强制带 WHERE tenant_id = :tenant_id
  4. 跨租户访问 → 403 Forbidden
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

    # 也支持 X-Tenant-Id 头注入（用于服务间调用）
    x_tenant = request.headers.get("X-Tenant-Id")
    if x_tenant and tenant_context.get() is None:
        set_tenant_context(TenantContext(tenant_id=x_tenant))

    try:
        response = await call_next(request)
    finally:
        clear_tenant_context()
    return response


def require_tenant_header(x_tenant_id: str | None = Header(None)) -> str:
    """依赖项：要求请求头携带 X-Tenant-Id（用于 API Key 服务间调用）."""
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Tenant-Id 头部缺失",
        )
    return x_tenant_id
