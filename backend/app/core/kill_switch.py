"""Kill Switch - 模型安全熔断开关（D03 4.5 + PIPL/EU AI Act 合规要求）.

设计原则：
  - 全局开关（Redis key `kill_switch:active`，不限租户）
  - fail-open：Redis 不可用时 is_active() 返回 False（不阻塞服务），仅 log warning
  - 激活后 RiskService.predict 返回安全降级结果（risk_score=50, model_version=kill-switch-active）
  - 双版本：同步版（Celery 任务用）+ 异步版（FastAPI 端点用）

Redis 数据结构（Hash）：
  kill_switch:active -> {active: "1", reason: "...", activated_at: "...", activated_by: "..."}
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import redis as sync_redis_lib

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis key（全局，不限租户）
_KILL_SWITCH_KEY = "kill_switch:active"

# 同步 Redis 客户端单例（Celery 任务用）
_sync_redis: sync_redis_lib.Redis | None = None


def _get_sync_redis() -> sync_redis_lib.Redis | None:
    """获取同步 Redis 客户端单例.

    连接失败返回 None（fail-open，调用方需处理）。
    """
    global _sync_redis
    if _sync_redis is not None:
        return _sync_redis
    try:
        client = sync_redis_lib.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        # 测试连通性
        client.ping()
        _sync_redis = client
        return _sync_redis
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill Switch 同步 Redis 连接失败，fail-open | err=%s", e)
        return None


def _build_payload(active: bool, reason: str, operator_id: str) -> dict:
    """构造 Kill Switch 状态 payload."""
    return {
        "active": "1" if active else "0",
        "reason": reason,
        "activated_at": datetime.now(UTC).isoformat(),
        "activated_by": operator_id,
    }


def _parse_status(raw: str | None) -> dict:
    """解析 Redis 中的状态 JSON，返回标准化 dict."""
    default = {
        "active": False,
        "reason": "",
        "activated_at": "",
        "activated_by": "",
    }
    if not raw:
        return default
    try:
        data = json.loads(raw)
        return {
            "active": str(data.get("active", "0")) == "1",
            "reason": str(data.get("reason", "")),
            "activated_at": str(data.get("activated_at", "")),
            "activated_by": str(data.get("activated_by", "")),
        }
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Kill Switch 状态解析失败，返回默认（inactive） | err=%s", e)
        return default


# ===== 同步版本（Celery 任务用） =====


def is_active() -> bool:
    """检查 Kill Switch 是否激活（同步，fail-open）.

    Redis 不可用时返回 False（不阻塞服务）。
    """
    client = _get_sync_redis()
    if client is None:
        # fail-open：Redis 不可用不阻塞服务
        return False
    try:
        raw = client.get(_KILL_SWITCH_KEY)
        return _parse_status(raw)["active"]
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill Switch 读取失败，fail-open | err=%s", e)
        return False


def activate(reason: str, operator_id: str) -> None:
    """激活 Kill Switch（同步）."""
    payload = _build_payload(True, reason, operator_id)
    client = _get_sync_redis()
    if client is None:
        logger.warning("Kill Switch 激活失败：Redis 不可用 | reason=%s | op=%s", reason, operator_id)
        return
    try:
        client.set(_KILL_SWITCH_KEY, json.dumps(payload, ensure_ascii=False))
        logger.warning("Kill Switch 已激活 | reason=%s | operator=%s", reason, operator_id)
    except Exception as e:  # noqa: BLE001
        logger.error("Kill Switch 激活写入失败 | err=%s", e)


def deactivate(operator_id: str) -> None:
    """解除 Kill Switch（同步）."""
    client = _get_sync_redis()
    if client is None:
        logger.warning("Kill Switch 解除失败：Redis 不可用 | op=%s", operator_id)
        return
    try:
        client.delete(_KILL_SWITCH_KEY)
        logger.info("Kill Switch 已解除 | operator=%s", operator_id)
    except Exception as e:  # noqa: BLE001
        logger.error("Kill Switch 解除失败 | err=%s", e)


def get_status() -> dict:
    """返回当前状态 dict（同步）."""
    client = _get_sync_redis()
    if client is None:
        return {"active": False, "reason": "", "activated_at": "", "activated_by": ""}
    try:
        raw = client.get(_KILL_SWITCH_KEY)
        return _parse_status(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill Switch 状态读取失败 | err=%s", e)
        return {"active": False, "reason": "", "activated_at": "", "activated_by": ""}


# ===== 异步版本（FastAPI 端点用） =====


async def is_active_async() -> bool:
    """检查 Kill Switch 是否激活（异步，fail-open）."""
    from app.core.redis import get_redis
    client = get_redis()
    if client is None:
        return False
    try:
        raw = await client.get(_KILL_SWITCH_KEY)
        return _parse_status(raw)["active"]
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill Switch 异步读取失败，fail-open | err=%s", e)
        return False


async def activate_async(reason: str, operator_id: str) -> None:
    """激活 Kill Switch（异步）."""
    payload = _build_payload(True, reason, operator_id)
    from app.core.redis import get_redis
    client = get_redis()
    if client is None:
        logger.warning("Kill Switch 异步激活失败：Redis 不可用 | reason=%s | op=%s", reason, operator_id)
        return
    try:
        await client.set(_KILL_SWITCH_KEY, json.dumps(payload, ensure_ascii=False))
        logger.warning("Kill Switch 已激活（async） | reason=%s | operator=%s", reason, operator_id)
    except Exception as e:  # noqa: BLE001
        logger.error("Kill Switch 异步激活写入失败 | err=%s", e)


async def deactivate_async(operator_id: str) -> None:
    """解除 Kill Switch（异步）."""
    from app.core.redis import get_redis
    client = get_redis()
    if client is None:
        logger.warning("Kill Switch 异步解除失败：Redis 不可用 | op=%s", operator_id)
        return
    try:
        await client.delete(_KILL_SWITCH_KEY)
        logger.info("Kill Switch 已解除（async） | operator=%s", operator_id)
    except Exception as e:  # noqa: BLE001
        logger.error("Kill Switch 异步解除失败 | err=%s", e)


async def get_status_async() -> dict:
    """返回当前状态 dict（异步）."""
    from app.core.redis import get_redis
    client = get_redis()
    if client is None:
        return {"active": False, "reason": "", "activated_at": "", "activated_by": ""}
    try:
        raw = await client.get(_KILL_SWITCH_KEY)
        return _parse_status(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill Switch 异步状态读取失败 | err=%s", e)
        return {"active": False, "reason": "", "activated_at": "", "activated_by": ""}


def _reset_sync_singleton() -> None:
    """重置同步 Redis 单例（仅测试用）."""
    global _sync_redis
    _sync_redis = None
