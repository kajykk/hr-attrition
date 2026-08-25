"""PII Fernet 字段级加密模块（ADR-007，参考 D04 3.1），支持多钥轮换.

提供：
  - encrypt(plaintext)  → Fernet 加密字符串（始终使用当前主钥）
  - decrypt(ciphertext) → Fernet 解密字符串（主钥失败后按 PII_PREVIOUS_KEYS 顺序回退）
  - hash_field(plaintext) → HMAC-SHA256 检索哈希（与 security.pii_hash 同源 pepper 链）

密钥来源：
  - 当前主钥：settings.PII_FERNET_KEY（加密与哈希派生唯一来源）
  - 历史密钥：settings.PII_PREVIOUS_KEYS（逗号分隔，新→旧排列；仅用于解密存量
    密文，命中时以 debug 日志标记 legacy_key_used=true。历史密钥绝不参与加密
    或哈希派生）

轮换流程（对标 bysj PII_PREVIOUS_KEYS 模式）：
  1. 新钥写入 PII_FERNET_KEY，旧钥移入 PII_PREVIOUS_KEYS；
  2. 运行 backend/scripts/rotate_pii_key.py 批量重加密存量员工 PII 列；
  3. 确认无 legacy 命中日志后清空 PII_PREVIOUS_KEYS。

PII 加密清单（D04 3.1）：
  name / id_card / phone / salary / ethnicity / disability
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import pii_hash

logger = get_logger(__name__)

# 主钥实例缓存（按配置值失效：settings 被替换/轮换后自动重建）
_primary_fernet: Fernet | None = None
_primary_fernet_src: str | None = None

# 历史密钥实例列表缓存（同样按原始配置串失效）
_previous_fernets: list[Fernet] = []
_previous_fernets_src: str | None = None


def _key_to_str(key: str | bytes) -> str:
    return key.decode("utf-8") if isinstance(key, bytes) else str(key)


def _get_primary() -> Fernet:
    """懒加载当前主钥 Fernet 实例（PII_FERNET_KEY；配置变更后自动重建）."""
    global _primary_fernet, _primary_fernet_src
    key_str = _key_to_str(settings.PII_FERNET_KEY)
    if _primary_fernet is None or _primary_fernet_src != key_str:
        _primary_fernet = Fernet(key_str.encode("utf-8"))
        _primary_fernet_src = key_str
    return _primary_fernet


def _get_previous_fernets() -> list[Fernet]:
    """解析 PII_PREVIOUS_KEYS（逗号分隔，新→旧；空段/重复/非法段跳过并告警）."""
    global _previous_fernets, _previous_fernets_src
    raw = str(getattr(settings, "PII_PREVIOUS_KEYS", "") or "")
    if _previous_fernets_src == raw:
        return _previous_fernets

    fernets: list[Fernet] = []
    seen: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part or part in seen:
            continue
        seen.add(part)
        try:
            fernets.append(Fernet(part.encode("utf-8")))
        except (ValueError, TypeError) as exc:
            logger.warning(
                "PII_PREVIOUS_KEYS 含非法密钥段（已跳过）: %s", type(exc).__name__
            )
    _previous_fernets = fernets
    _previous_fernets_src = raw
    return fernets


def encrypt(plaintext: str) -> str:
    """Fernet 加密（始终使用当前主钥 PII_FERNET_KEY）.

    Args:
        plaintext: 明文（UTF-8 字符串）。

    Returns:
        Fernet token 字符串（UTF-8 可打印）。
    """
    f = _get_primary()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _try_decrypt(f: Fernet, token: bytes) -> str | None:
    """单钥尝试解密；失败返回 None（不抛异常）."""
    try:
        return f.decrypt(token).decode("utf-8")
    except InvalidToken:
        return None


def decrypt(ciphertext: str) -> str:
    """Fernet 解密：先试主钥，失败按 PII_PREVIOUS_KEYS 声明顺序依次回退.

    Args:
        ciphertext: Fernet token 字符串。

    Returns:
        解密后的明文。

    Raises:
        cryptography.fernet.InvalidToken: 主钥与全部历史钥均无法匹配时抛出
            （与 security.decrypt_pii 不同，此处不吞异常，便于上层精确处理）。
    """
    token = ciphertext.encode("utf-8")
    plaintext = _try_decrypt(_get_primary(), token)
    if plaintext is not None:
        return plaintext

    for idx, legacy in enumerate(_get_previous_fernets()):
        plaintext = _try_decrypt(legacy, token)
        if plaintext is None:
            continue
        logger.debug(
            "pii_decrypt legacy_key_used=true previous_key_index=%d "
            "(建议运行 scripts/rotate_pii_key.py 完成存量重加密)",
            idx,
        )
        return plaintext

    raise InvalidToken(
        "PII 解密失败：主钥与全部历史钥（PII_PREVIOUS_KEYS）均无法匹配该密文"
    )


def hash_field(plaintext: str) -> str:
    """检索索引哈希（HMAC-SHA256 + pepper，与 security.pii_hash 同源）.

    pepper 回退链保持不变：优先 PII_HASH_PEPPER，缺失时从**当前主钥**
    PII_FERNET_KEY 派生（不使用历史密钥）。因此固定显式 pepper 时，
    本哈希跨 Fernet 密钥轮换稳定。
    """
    hashed = pii_hash(plaintext)
    if hashed is None:  # pragma: no cover - plaintext 非 None 时 pii_hash 必返 str
        raise ValueError("pii_hash 对非 None 输入应返回字符串")
    return hashed
