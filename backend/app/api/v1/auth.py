"""认证路由（D05 3.1）."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token, create_refresh_token, decode_token, verify_password,
)
from app.db.session import get_db
from app.models.user import ROLE_ADMIN, User
from app.schemas.auth import (
    LoginRequest, LoginResult, RefreshRequest, RefreshResponse, UserOut,
)

router = APIRouter()


@router.post("/login", response_model=LoginResult)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录（D05 3.1 POST /auth/login）.

    返回 access_token (30min) + refresh_token (7d) + user 信息。
    """
    stmt = select(User).where(
        User.email == payload.email,
        User.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")

    # 管理员强制 2FA（D03 6.1）
    if user.role == ROLE_ADMIN and user.totp_secret and not payload.totp_code:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员需提供 2FA 验证码")

    access = create_access_token(str(user.id), str(user.tenant_id), user.role)
    refresh = create_refresh_token(str(user.id), str(user.tenant_id))

    return LoginResult(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        user=UserOut(
            id=user.id,
            name=user.name,
            role=user.role,
            tenant_id=user.tenant_id,
            email=user.email,
        ),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(payload: RefreshRequest):
    """刷新令牌（D05 3.1 POST /auth/refresh）."""
    try:
        decoded = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_token 无效")

    if decoded.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 类型错误")

    user_id = decoded.get("sub")
    tenant_id = decoded.get("tenant_id")
    role = decoded.get("role", "hrbp")
    access = create_access_token(user_id, tenant_id, role)
    return RefreshResponse(access_token=access, expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60)
