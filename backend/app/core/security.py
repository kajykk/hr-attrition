"""安全模块 - JWT + 密码哈希 + PII Fernet 字段级加密（ADR-007）+ PII HMAC 检索哈希."""
import hashlib
import hmac as _hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

# ===== 密码哈希（bcrypt + salt） =====
# passlib 与 bcrypt 5.x 不兼容，直接使用 bcrypt 库
import bcrypt as _bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import jwt

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def hash_password(password: str) -> str:
    """对密码进行 bcrypt 哈希."""
    salt = _bcrypt.gensalt()
    return _bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码与哈希是否匹配."""
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ===== JWT（access 30min / refresh 7d，参考 D03 6.1） =====
def create_access_token(
    subject: str, tenant_id: str, role: str, extra: dict | None = None
) -> str:
    """创建 access token."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, tenant_id: str) -> str:
    """创建 refresh token.

    携带 jti（唯一 ID）：/auth/refresh 轮换时旧 jti 进 Redis 黑名单，
    实现"一次性使用 + 重放检测"。
    """
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并校验 JWT，失败抛出 JWTError."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


# ===== PII Fernet 字段级加密（ADR-007） =====
# 复用 DWS pii_crypto.py 思路：Fernet 对称加密，密钥季度轮换
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """懒加载 Fernet 实例（密钥来自 PII_FERNET_KEY 环境变量）."""
    global _fernet
    if _fernet is None:
        key = settings.PII_FERNET_KEY.encode() if isinstance(settings.PII_FERNET_KEY, str) else settings.PII_FERNET_KEY
        _fernet = Fernet(key)
    return _fernet


def encrypt_pii(plaintext: str | None) -> str | None:
    """PII 字段加密：返回 Fernet token 字符串。None 输入返回 None."""
    if plaintext is None:
        return None
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_pii(ciphertext: str | None) -> str | None:
    """PII 字段解密。None 输入返回 None；非法 token 返回 None."""
    if ciphertext is None:
        return None
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# ===== PII HMAC 检索哈希（pepper 加固，替代裸 SHA256） =====
# 裸 SHA256 可被彩虹表/字典预计算；HMAC-SHA256 + 服务端 pepper 使离线
# 碰撞不可行。pepper 轮换会使存量 *_hash 索引失效，需离线重算迁移。
_pepper_cache: bytes | None = None
_pepper_warned = False


def _pii_hash_pepper() -> bytes:
    """获取 PII 检索哈希 pepper.

    优先 PII_HASH_PEPPER 环境变量；缺失时从 PII_FERNET_KEY 派生（SHA256）
    并 warning 提示配置独立 pepper。
    """
    global _pepper_cache, _pepper_warned
    if _pepper_cache is not None:
        return _pepper_cache

    pepper = (getattr(settings, "PII_HASH_PEPPER", "") or "").strip()
    if pepper:
        _pepper_cache = pepper.encode("utf-8")
        return _pepper_cache

    # 回退：从 Fernet key 派生域分隔摘要（避免直接复用加密密钥本体）
    if not _pepper_warned:
        logger.warning(
            "PII_HASH_PEPPER 未配置，回退使用 PII_FERNET_KEY 派生 pepper"
            "（建议配置独立随机 pepper，更换会使存量 *_hash 失效需重算）"
        )
        _pepper_warned = True
    derived = hashlib.sha256(b"hra:pii-hash-pepper:" + settings.PII_FERNET_KEY.encode("utf-8")).hexdigest()
    _pepper_cache = derived.encode("utf-8")
    return _pepper_cache


def pii_hash(plaintext: str | None) -> str | None:
    """PII 字段哈希（HMAC-SHA256 + pepper，用于检索索引）.

    注意：与历史裸 SHA256 哈希不兼容，切换后需按新算法重算存量 name_hash 等
    索引列，否则等值检索会漏配。
    """
    if plaintext is None:
        return None
    return _hmac.new(
        _pii_hash_pepper(), plaintext.encode("utf-8"), hashlib.sha256
    ).hexdigest()
