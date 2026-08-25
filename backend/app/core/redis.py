"""Redis 异步客户端（模块级单例，参考 D03 2.4 缓存层）.

提供：
  - get_redis() -> Redis：返回模块级异步 Redis 实例（连接失败降级为 None）
  - init_redis() / close_redis()：生命周期函数（在 main.py lifespan 调用）

降级策略：连接失败不崩溃，RiskService 检测到 None 则跳过缓存（D03 ADR-006）。
"""
from __future__ import annotations

import re

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 模块级异步 Redis 客户端单例
_redis_client: aioredis.Redis | None = None

# 凭据脱敏：redis://user:pass@host → redis://***@host（日志不得泄露凭据）
_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)([^@/?\s]+)@")


def mask_url_credentials(url: str | None) -> str:
    """抹掉 URL 中的凭据段（://user:pass@ → ://***@），用于安全打日志."""
    if not url:
        return ""
    return _URL_CREDENTIALS_RE.sub(r"\g<scheme>***@", url)


async def init_redis() -> None:
    """初始化 Redis 连接（应用启动时调用）.

    连接失败不抛异常，仅 log warning，应用继续启动（降级无缓存模式）。
    """
    global _redis_client
    if _redis_client is not None:
        return
    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        # 测试连通性（PING）
        await client.ping()
        _redis_client = client
        logger.info("Redis 连接成功 | url=%s", mask_url_credentials(settings.REDIS_URL))
    except Exception as e:  # noqa: BLE001
        # 降级：连接失败不崩溃，RiskService 检测到 None 跳过缓存
        logger.warning(
            "Redis 连接失败，降级为无缓存模式 | url=%s | err=%s",
            mask_url_credentials(settings.REDIS_URL),
            e,
        )
        _redis_client = None


async def close_redis() -> None:
    """关闭 Redis 连接（应用关闭时调用）."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 关闭异常 | err=%s", e)
        finally:
            _redis_client = None


def get_redis() -> aioredis.Redis | None:
    """返回模块级 Redis 客户端实例.

    Returns:
        Redis 实例；未初始化或连接失败时返回 None（调用方需自行处理 None）。
    """
    return _redis_client


# 二进制安全客户端单例（decode_responses=False）
_binary_client: aioredis.Redis | None = None


def get_redis_binary() -> aioredis.Redis | None:
    """返回二进制安全（decode_responses=False）的 Redis 客户端.

    用途：文件字节缓存（rag:file:*）、embedding 向量（struct.pack 二进制）等
    非文本值。共享客户端开启 decode_responses=True 时：
      - GET 二进制值会因 UTF-8 解码失败抛异常；
      - GET 文本值返回 str，调用方若按 bytes 处理会得到 'str' has no 'decode' 类错误。

    懒创建：from_url 不立即建连，首次命令才拨号；创建异常返回 None 由调用方降级。
    """
    global _binary_client
    if _binary_client is None:
        try:
            _binary_client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 二进制客户端创建失败 | err=%s", e)
            return None
    return _binary_client
