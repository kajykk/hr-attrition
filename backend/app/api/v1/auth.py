"""认证路由（D05 3.1）."""
from datetime import datetime, timezone

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import login_limit
from app.core.security import (
    create_access_token, create_refresh_token, decode_token, verify_password,
)
from app.db.session import get_db
from app.models.user import ROLE_ADMIN, User
from app.schemas.auth import (
    LoginRequest, LoginResult, RefreshRequest, RefreshResponse, UserOut,
)
from app.services.audit_service import append_audit_log

router = APIRouter()
logger = get_logger(__name__)


async def _log_auth_event(
    db: AsyncSession,
    user: User,
    action: str,
    reason: str | None = None,
    commit: bool = False,
) -> None:
    """写认证审计日志（best-effort，失败不阻断登录流程）.

    commit=True：立即提交。登录失败路径随后抛 HTTPException，get_db 会
    rollback 整个事务，若不提前提交则审计记录会丢失（冒烟发现的缺陷）。
    """
    try:
        await append_audit_log(
            db=db,
            tenant_id=user.tenant_id,
            action=action,
            resource_type="auth",
            user_id=user.id,
            after_value={"reason": reason} if reason else None,
        )
        if commit:
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("认证审计日志写入失败 | action=%s | err=%s", action, e)


def _verify_totp(user: User, code: str | None) -> None:
    """校验管理员 2FA（TOTP，pyotp RFC 6238）.

    user.totp_secret 非空时强制校验：验证码缺失或不匹配 → 401。
    """
    if user.role == ROLE_ADMIN and user.totp_secret:
        if not code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="管理员需提供 2FA 验证码",
            )
        try:
            valid = pyotp.TOTP(user.totp_secret).verify(code, valid_window=1)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="2FA 验证码无效或已过期",
            )


@router.post("/login", response_model=LoginResult)
@login_limit()
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """用户登录（D05 3.1 POST /auth/login）.

    返回 access_token (30min) + refresh_token (7d) + user 信息。
    管理员启用 TOTP 时强制校验 2FA；登录成功更新 last_login_at/ip。
    限流：按 IP RATE_LIMIT_LOGIN（默认 5/minute），防密码爆破。
    """
    stmt = select(User).where(
        User.email == payload.email,
        User.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        # 用户存在但密码错 → 写审计（用户不存在时无法归属租户，跳过）
        if user is not None:
            await _log_auth_event(db, user, "auth.login_failed", reason="bad_credentials", commit=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    if user.status != "active":
        await _log_auth_event(db, user, "auth.login_failed", reason="disabled", commit=True)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")

    # 管理员强制 2FA（D03 6.1，TOTP 真校验）
    try:
        _verify_totp(user, payload.totp_code)
    except HTTPException:
        await _log_auth_event(db, user, "auth.login_failed", reason="invalid_totp", commit=True)
        raise

    # 登录成功：更新 last_login_at + 写审计
    user.last_login_at = datetime.now(timezone.utc)
    await _log_auth_event(db, user, "auth.login")
    await db.flush()

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
async def refresh_token(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """刷新令牌（D05 3.1 POST /auth/refresh）.

    除 JWT 解码外，还校验用户仍存在且 active（防止已删除/禁用用户续期）。
    """
    try:
        decoded = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_token 无效")

    if decoded.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 类型错误")

    user_id = decoded.get("sub")
    tenant_id = decoded.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 缺少用户/租户信息")

    # 校验用户仍存在且 active
    stmt = select(User).where(
        User.id == user_id,
        User.tenant_id == tenant_id,
        User.deleted_at.is_(None),
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已删除")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")

    access = create_access_token(user_id, tenant_id, user.role)
    return RefreshResponse(access_token=access, expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60)
