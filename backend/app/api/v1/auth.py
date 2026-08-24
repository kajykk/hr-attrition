"""认证路由（D05 3.1）.

安全机制：
  - 登录限流（slowapi 按 IP）
  - 连续失败锁定：failed_login_count ≥ 5 → locked_until = now + 15min
  - 管理员 TOTP 2FA：secret 落库 Fernet 加密，读取解密（兼容存量明文，
    检测到明文时写回加密值）
  - refresh token 轮换：旧 jti 进 Redis 黑名单（SET NX EX=剩余有效期），
    重放旧 token 即命中黑名单拒绝（jti 复用检测）
"""
import time
from datetime import UTC, datetime, timedelta

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pii_crypto
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import login_limit
from app.core.redis import get_redis
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.db.session import get_db
from app.models.user import ROLE_ADMIN, User
from app.schemas.auth import (
    LoginRequest,
    LoginResult,
    RefreshRequest,
    RefreshResponse,
    UserOut,
)
from app.services.audit_service import append_audit_log

router = APIRouter()
logger = get_logger(__name__)

# refresh token 轮换黑名单 key 前缀（Redis）：auth:rt_bl:{jti} = "1"
_REFRESH_BLACKLIST_PREFIX = "auth:rt_bl:"


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


def _load_totp_secret(user: User) -> str | None:
    """读取并解密 TOTP secret（兼容存量明文）.

    - Fernet 解密成功 → 返回明文 secret
    - 解密失败 → 视为存量明文直接使用，并**写回加密值**（渐进迁移）
    - 空值 → None
    """
    stored = user.totp_secret
    if not stored:
        return None
    try:
        return pii_crypto.decrypt(stored)
    except Exception:  # noqa: BLE001  # InvalidToken 等：存量明文兼容
        logger.info("TOTP secret 为存量明文，写回加密值 | user_id=%s", getattr(user, "id", "?"))
        user.totp_secret = pii_crypto.encrypt(stored)
        return stored


def _verify_totp(user: User, code: str | None) -> None:
    """校验管理员 2FA（TOTP，pyotp RFC 6238）.

    user.totp_secret 非空时强制校验：验证码缺失或不匹配 → 401。
    secret 从库中解密读取；检测到存量明文时由 _load_totp_secret 写回加密值。
    """
    if user.role == ROLE_ADMIN and user.totp_secret:
        if not code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="管理员需提供 2FA 验证码",
            )
        try:
            valid = pyotp.TOTP(_load_totp_secret(user)).verify(code, valid_window=1)
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
    防爆破：连续失败 ≥ LOGIN_MAX_FAILED_ATTEMPTS（默认 5）次锁定
    LOGIN_LOCKOUT_MINUTES（默认 15 分钟），期间直接拒绝（不泄露密码对错）。
    """
    stmt = select(User).where(
        User.email == payload.email,
        User.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # 锁定检查：locked_until 未到期 → 拒绝（先于密码校验，避免给爆破方探针）
    now = datetime.now(UTC)
    if user is not None and user.locked_until is not None:
        if user.locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"账号已临时锁定，请于 {settings.LOGIN_LOCKOUT_MINUTES} 分钟后重试",
            )
        # 锁定已过期：解除并清零计数，继续正常流程
        user.locked_until = None
        user.failed_login_count = 0

    if user is None or not verify_password(payload.password, user.password_hash):
        if user is not None:
            # 连续失败计数 + 阈值锁定（字段持久化在 users 表）
            user.failed_login_count = (user.failed_login_count or 0) + 1
            reason = "bad_credentials"
            if user.failed_login_count >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                user.failed_login_count = 0  # 解锁后重新计数
                reason = "account_locked"
                logger.warning(
                    "登录连续失败达到阈值，账号临时锁定 | user=%s | lock_minutes=%s",
                    user.id, settings.LOGIN_LOCKOUT_MINUTES,
                )
            await _log_auth_event(db, user, "auth.login_failed", reason=reason, commit=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    if user.status != "active":
        await _log_auth_event(db, user, "auth.login_failed", reason="disabled", commit=True)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")

    # 管理员强制 2FA（D03 6.1，TOTP 真校验；secret 加密读取+明文写回）
    try:
        _verify_totp(user, payload.totp_code)
    except HTTPException:
        await _log_auth_event(db, user, "auth.login_failed", reason="invalid_totp", commit=True)
        raise

    # 登录成功：重置失败计数/锁定 + 更新 last_login_at + 写审计
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
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


async def _blacklist_refresh_jti(jti: str, exp: int | float) -> bool:
    """将 refresh token 的 jti 加入 Redis 黑名单（轮换核心）.

    SET NX EX=剩余有效期：key 在 token 自然过期后自动清除，不占无限内存。
    NX 语义保证同一 jti 只能成功轮换一次——并发重放时第二个请求 SET 失败。

    Returns:
        True = 首次吊销成功；False = jti 已存在（复用/并发竞态）。
        Redis 不可用时返回 True（fail-open：轮换继续，重放检测降级）。
    """
    redis = get_redis()
    if redis is None:
        logger.warning("Redis 不可用，refresh 轮换黑名单降级（无法检测重放）")
        return True
    ttl = max(1, int(float(exp) - time.time()))
    try:
        # decode_responses=True 客户端：set 返回 True/None
        was_set = await redis.set(f"{_REFRESH_BLACKLIST_PREFIX}{jti}", "1", ex=ttl, nx=True)
        return bool(was_set)
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh 黑名单写入失败（降级放行） | err=%s", e)
        return True


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """刷新令牌（D05 3.1 POST /auth/refresh）.

    除 JWT 解码外，还校验用户仍存在且 active（防止已删除/禁用用户续期）。

    轮换机制：
      1. 解码取 jti；黑名单命中 → 旧 token 重放，401 拒绝
      2. 校验通过后将旧 jti 以剩余有效期为 TTL 写入黑名单（NX 保证一次性）
      3. 签发新 access + 新 refresh（响应回发新 refresh_token）
    """
    try:
        decoded = decode_token(payload.refresh_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_token 无效")

    if decoded.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 类型错误")

    user_id = decoded.get("sub")
    tenant_id = decoded.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 缺少用户/租户信息")

    # 无 jti 的历史版本 token：无法纳入轮换追踪，直接拒绝（强制重新登录换取新格式）
    jti = decoded.get("jti")
    if not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_token 缺少 jti，请重新登录")

    # jti 复用检测：黑名单命中即视为重放（该 token 已被使用过）
    redis = get_redis()
    if redis is not None:
        try:
            replayed = await redis.get(f"{_REFRESH_BLACKLIST_PREFIX}{jti}")
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh 黑名单查询失败（降级放行） | err=%s", e)
            replayed = None
        if replayed:
            logger.warning(
                "refresh token 重放检测命中 | user=%s | jti=%s", user_id, jti,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="refresh_token 已使用或被吊销",
            )

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

    # 轮换：先吊销旧 jti（NX 失败 = 并发重放），再签发新令牌对
    first_use = await _blacklist_refresh_jti(jti, decoded.get("exp", time.time()))
    if not first_use:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh_token 已使用或被吊销",
        )

    access = create_access_token(user_id, tenant_id, user.role)
    new_refresh = create_refresh_token(user_id, tenant_id)
    return RefreshResponse(
        access_token=access,
        expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        refresh_token=new_refresh,
    )
