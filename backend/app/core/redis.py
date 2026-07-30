"""Redis 异步客户端（模块级单例，参考 D03 2.4 缓存层）.

提供：
  - get_redis() -> Redis：返回模块级异步 Redis 实例（连接失败降级为 None）
  - init_redis() / close_redis()：生命周期函数（在 main.py lifespan 调用）

降级策略：连接失败不崩溃，RiskService 检测到 None 则跳过缓存（D03 ADR-006）。
"""
from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 模块级异步 Redis 客户端单例
_redis_client: Optional[aioredis.Redis] = None


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
        logger.info("Redis 连接成功 | url=%s", settings.REDIS_URL)
    except Exception as e:  # noqa: BLE001
        # 降级：连接失败不崩溃，RiskService 检测到 None 跳过缓存
        logger.warning("Redis 连接失败，降级为无缓存模式 | url=%s | err=%s", settings.REDIS_URL, e)
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


def get_redis() -> Optional[aioredis.Redis]:
    """返回模块级 Redis 客户端实例.

    Returns:
        Redis 实例；未初始化或连接失败时返回 None（调用方需自行处理 None）。
    """
    return _redis_client
