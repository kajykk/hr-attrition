"""PII Fernet 字段级加密模块（ADR-007，参考 D04 3.1）.

提供：
  - encrypt(plaintext)  → Fernet 加密字符串
  - decrypt(ciphertext) → Fernet 解密字符串
  - hash_field(plaintext) → SHA256 哈希（用于检索索引）

PII 加密清单（D04 3.1）：
  name / id_card / phone / salary / ethnicity / disability

密钥来自 settings.PII_FERNET_KEY（模块级单例 Fernet 实例）。
"""
from __future__ import annotations

import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 模块级 Fernet 单例（懒加载，避免导入时立即校验密钥）
_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """懒加载 Fernet 实例（密钥来自 PII_FERNET_KEY 配置）."""
    global _fernet
    if _fernet is None:
        key = settings.PII_FERNET_KEY
        key_bytes = key.encode("utf-8") if isinstance(key, str) else key
        _fernet = Fernet(key_bytes)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Fernet 加密.

    Args:
        plaintext: 明文（UTF-8 字符串）。

    Returns:
        Fernet token 字符串（UTF-8 可打印）。
    """
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Fernet 解密.

    Args:
        ciphertext: Fernet token 字符串。

    Returns:
        解密后的明文。

    Raises:
        cryptography.fernet.InvalidToken: token 非法时抛出（与 security.decrypt_pii 不同，
            此处不吞异常，便于上层精确处理）。
    """
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        # 兼容旧密钥轮换场景，调用方应捕获并处理
        raise


def hash_field(plaintext: str) -> str:
    """SHA256 哈希（用于检索索引，参考 D04 3.1 name_hash 字段）.

    Args:
        plaintext: 明文。

    Returns:
        64 位十六进制 SHA256 摘要字符串。
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
