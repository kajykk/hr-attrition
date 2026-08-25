"""预警自动升级任务 - FR-WARN-004 / D04 4.2 升级机制（状态机五项补齐 2/4）.

Celery beat 每 6h 扫描一次（escalate_stale_warnings）：
  - open/unacknowledged（status=new，即从未确认）超过 24h → 等级升一级（封顶 P0）
  - 超过 48h → escalated_to 写入当前最高处理角色（租户内 HR 经理）并标记事件

设计：
  - plan_escalation 为纯函数（便于单测），DB 读写集中在 run_escalation_sweep
  - 行锁 FOR UPDATE ... SKIP LOCKED：与人工处理并发安全，多 worker 不重复升级
  - 单条失败不阻塞整批；DB 不可用整体降级 skipped
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.user import ROLE_HR_MANAGER, User
from app.models.warning import (
    LEVEL_P0,
    LEVEL_P1,
    LEVEL_P2,
    STATUS_NEW,
    WarningEvent,
    WarningRecord,
)
from app.services.warning_service import SYSTEM_OPERATOR_ID

logger = get_logger(__name__)

# 超 24h 未确认 → 等级升一级（封顶 P0）
STALE_ESCALATE_AFTER = timedelta(hours=24)
# 超 48h 未确认 → escalated_to 指向 HR 经理并标记
FINAL_ESCALATE_AFTER = timedelta(hours=48)

# 升级链：P2 → P1 → P0（封顶）
LEVEL_NEXT: dict[str, str] = {LEVEL_P2: LEVEL_P1, LEVEL_P1: LEVEL_P0, LEVEL_P0: LEVEL_P0}


def plan_escalation(warning: WarningRecord, now: datetime) -> dict | None:
    """计算单条过期未确认预警的升级动作（纯函数，不改库）.

    返回:
        {"new_level": str | None, "need_manager": bool}；无需任何动作时返回 None。
        new_level 非空表示等级需升一级；need_manager=True 表示需写 escalated_to。
    """
    created_at = warning.created_at
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        # DB 时区缺失兜底：按 UTC 解释（列定义为 timezone=True，正常不会走到）
        created_at = created_at.replace(tzinfo=UTC)
    age = now - created_at
    if age < STALE_ESCALATE_AFTER:
        return None

    new_level = LEVEL_NEXT.get(warning.level)
    if new_level == warning.level:
        new_level = None  # 已封顶 P0
    need_manager = age >= FINAL_ESCALATE_AFTER
    if new_level is None and not need_manager:
        return None
    return {"new_level": new_level, "need_manager": need_manager}


async def _find_hr_manager_id(db: AsyncSession, tenant_id) -> object | None:
    """查询租户内一名在岗 HR 经理（升级目标，按创建时间最早优先）."""
    stmt = (
        select(User.id)
        .where(
            User.tenant_id == tenant_id,
            User.role == ROLE_HR_MANAGER,
            User.status == "active",
            User.deleted_at.is_(None),
        )
        .order_by(User.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def run_escalation_sweep(now: datetime | None = None) -> dict:
    """扫描全部过期未确认预警并执行升级（单事务，返回统计）."""
    now = now or datetime.now(UTC)
    stats = {
        "status": "ok",
        "checked": 0,
        "level_upgraded": 0,
        "final_escalated": 0,
        "checked_at": now.isoformat(),
    }

    async with async_session_factory() as session:
        cutoff = now - STALE_ESCALATE_AFTER
        stmt = (
            select(WarningRecord)
            .where(
                WarningRecord.status == STATUS_NEW,
                WarningRecord.created_at <= cutoff,
            )
            .with_for_update(skip_locked=True)
        )
        stale = list((await session.execute(stmt)).scalars().all())
        stats["checked"] = len(stale)

        manager_cache: dict[object, object] = {}
        for w in stale:
            try:
                plan = plan_escalation(w, now)
                if plan is None:
                    continue
                if plan["new_level"] is not None:
                    old_level = w.level
                    w.level = plan["new_level"]
                    session.add(WarningEvent(
                        tenant_id=w.tenant_id,
                        warning_id=w.id,
                        action="escalated",
                        from_status=w.status,
                        to_status=w.status,
                        operator_id=SYSTEM_OPERATOR_ID,
                        comment=(
                            f"自动升级（超 {int(STALE_ESCALATE_AFTER.total_seconds() // 3600)}h "
                            f"未确认）：{old_level} → {plan['new_level']}"
                        ),
                        created_at=now,
                    ))
                    stats["level_upgraded"] += 1
                if plan["need_manager"]:
                    if w.tenant_id not in manager_cache:
                        manager_cache[w.tenant_id] = await _find_hr_manager_id(session, w.tenant_id)
                    manager_id = manager_cache[w.tenant_id]
                    if manager_id is not None and w.escalated_to != manager_id:
                        w.escalated_to = manager_id
                        session.add(WarningEvent(
                            tenant_id=w.tenant_id,
                            warning_id=w.id,
                            action="escalated",
                            from_status=w.status,
                            to_status=w.status,
                            operator_id=SYSTEM_OPERATOR_ID,
                            comment=f"终态升级（超 {int(FINAL_ESCALATE_AFTER.total_seconds() // 3600)}h 未确认）：升级至 HR 经理",
                            created_at=now,
                        ))
                        stats["final_escalated"] += 1
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "预警自动升级单条失败（不阻塞整批） | warning_id=%s | err=%s", w.id, e
                )

        await session.commit()

    logger.info(
        "预警自动升级完成 | checked=%d | level_upgraded=%d | final_escalated=%d",
        stats["checked"], stats["level_upgraded"], stats["final_escalated"],
    )
    return stats


@celery_app.task(name="app.tasks.warning_escalation.escalate_stale_warnings")
def escalate_stale_warnings() -> dict:
    """自动升级过期未确认预警（Celery beat 每 6h 触发）.

    Returns:
        {status: "ok"|"skipped", checked, level_upgraded, final_escalated, checked_at}
    """
    try:
        return asyncio.run(run_escalation_sweep())
    except Exception as e:  # noqa: BLE001
        logger.error("预警自动升级任务执行失败 | err=%s", e)
        return {
            "status": "skipped",
            "reason": f"DB 不可用: {e}",
            "checked_at": datetime.now(UTC).isoformat(),
        }
