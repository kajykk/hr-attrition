"""审计服务 - 哈希链防篡改（D04 3.6 + D03 6.3）.

哈希链规则：
  current_hash = sha256(prev_hash + payload_json + created_at_iso)
  首条 prev_hash = "0" * 64（创世）
  任何篡改导致后续 current_hash 校验失败
"""
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.audit_log import AuditLog

logger = get_logger(__name__)

GENESIS_HASH = "0" * 64  # 哈希链创世 prev_hash


def _compute_hash(prev_hash: str, payload: dict, created_at: datetime) -> str:
    """计算当前条哈希."""
    raw = (
        prev_hash
        + json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        + created_at.isoformat()
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def append_audit_log(
    db: AsyncSession,
    tenant_id: UUID,
    action: str,
    resource_type: str,
    user_id: UUID | None = None,
    resource_id: UUID | None = None,
    before_value: dict | None = None,
    after_value: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """追加审计日志（自动维护哈希链）.

    流程：
      1. 查询当前租户最新一条审计日志的 current_hash 作为 prev_hash
      2. 计算当前条 current_hash
      3. 插入新记录
    """
    # 获取上一条 current_hash（按租户隔离）
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    last_log = result.scalar_one_or_none()
    prev_hash = last_log.current_hash if last_log else GENESIS_HASH

    now = datetime.now(UTC)
    payload = {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id) if user_id else None,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id else None,
        "before_value": before_value,
        "after_value": after_value,
    }
    current_hash = _compute_hash(prev_hash, payload, now)

    log = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        ip=ip,
        user_agent=user_agent,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_value=before_value,
        after_value=after_value,
        prev_hash=prev_hash,
        current_hash=current_hash,
        created_at=now,
    )
    db.add(log)
    await db.flush()
    return log


async def verify_hash_chain(db: AsyncSession, tenant_id: UUID) -> bool:
    """校验租户审计日志哈希链完整性."""
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    prev = GENESIS_HASH
    for log in logs:
        if log.prev_hash != prev:
            logger.error("哈希链断裂：log_id=%s 期望 prev=%s 实际 prev=%s", log.id, prev, log.prev_hash)
            return False
        payload = {
            "tenant_id": str(log.tenant_id),
            "user_id": str(log.user_id) if log.user_id else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": str(log.resource_id) if log.resource_id else None,
            "before_value": log.before_value,
            "after_value": log.after_value,
        }
        expected = _compute_hash(prev, payload, log.created_at)
        if log.current_hash != expected:
            logger.error("哈希校验失败：log_id=%s", log.id)
            return False
        prev = log.current_hash
    return True
