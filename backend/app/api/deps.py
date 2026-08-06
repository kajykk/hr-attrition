"""API 依赖：get_current_user / require_role（RBAC 5 角色，D03 6.1）."""
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.core.tenant import get_current_tenant_id
from app.db.session import get_db
from app.models.user import User


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 Authorization Bearer token 解析当前用户.

    校验流程：
      1. 头部必须 Bearer token
      2. JWT 解码（type=access）
      3. 查询用户表（含 tenant_id 隔离）
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供 Authorization Bearer token",
        )
    token = authorization[7:].strip()
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT 解码失败或已过期",
        )

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 类型错误")

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 缺少用户/租户信息")

    stmt = select(User).where(
        User.id == UUID(user_id),
        User.tenant_id == UUID(tenant_id),
        User.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已删除")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已禁用")

    return user


def require_role(*roles: str):
    """角色守卫依赖工厂.

    用法：require_role(ROLE_ADMIN, ROLE_HR_MANAGER)
    """

    async def _guard(user: User = Depends(get_current_user)) -> User:
        if roles and user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"角色 {user.role} 无权访问（需要：{list(roles)}）",
            )
        return user

    return _guard


def get_tenant_id_from_context() -> UUID:
    """从租户上下文获取 tenant_id（与 get_current_user 配合）."""
    tid = get_current_tenant_id()
    return UUID(tid)
