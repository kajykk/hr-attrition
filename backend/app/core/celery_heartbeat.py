"""Celery Worker/Beat 心跳（P2-11 健康检查真实化).

原先 /health 端点 `components["celery"] = "healthy"` 是硬编码。
现在改为：Celery Beat 每 5 分钟写一次 Redis key `celery:heartbeat`（ISO 时间戳），
   健康检查读取该 key 判断 worker/beat 存活度。

Key：`celery:heartbeat` -> ISO8601 UTC
判定：key 存在且距今 < 10 分钟 → healthy；Redis 不可用 → not_configured(fail-open)。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

_CELERY_HEARTBEAT_KEY = "celery:heartbeat"
# 心跳任务间隔 5min，超过该阈值视为失联（留一倍余量）
_HEARTBEAT_MAX_AGE_SECONDS = 600


def write_heartbeat(redis_client) -> None:
    """写入心跳时间戳（由 Celery 任务调用）.

    Args:
        redis_client: 同步 Redis 客户端；为 None 时静默跳过（fail-open）。
    """
    if redis_client is None:
        return
    try:
        payload = json.dumps(
            {"ts": datetime.now(UTC).isoformat()}, ensure_ascii=False
        )
        # 带 TTL（阈值+余量），beat 失联后 key 自动过期自清理
        redis_client.setex(
            _CELERY_HEARTBEAT_KEY, _HEARTBEAT_MAX_AGE_SECONDS + 60, payload
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Celery 心跳写入失败 | err=%s", e)


def heartbeat_age_seconds(redis_client) -> float | None:
    """返回心跳距今秒数。

    Returns:
        心跳年龄（秒）。key 不存在 / 不可解析 / Redis 不可用 → None
        （由调用方决定降级为 not_configured 或 degraded）。
    """
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(_CELERY_HEARTBEAT_KEY)
        if not raw:
            return None
        data = json.loads(raw)
        ts = datetime.fromisoformat(data["ts"])
        return (datetime.now(UTC) - ts).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def is_heartbeat_fresh(redis_client) -> bool | None:
    """心跳是否新鲜。

    Returns:
        True: 新鲜；False: key 过期/不存在/异常；
        None: Redis 不可用（调用方应标记 not_configured 而非 degraded）。
    """
    if redis_client is None:
        return None
    age = heartbeat_age_seconds(redis_client)
    if age is None:
        return False
    return age <= _HEARTBEAT_MAX_AGE_SECONDS
