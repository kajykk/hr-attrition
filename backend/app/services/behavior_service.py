"""行为事件服务 - 行为特征基建（README 路线图第一步）.

职责：
  1. record_behavior_event：单条行为事件写入（异步、best-effort，失败不阻断业务）
  2. record_behavior_events：批量写入（add_all 单事务，批量容忍：失败整体回滚并告警）
   3. 真实事件源接线：
      a) 登录成功（auth.py）→ record_login_event_for_user 记 login 事件
         （User 与 Employee 无外键，best-effort 按租户内 email 匹配员工，未匹配则跳过）
      b) 预警状态流转（warnings.py）→ record_warning_transition_event 记 warning_transition 事件
      c) 风险预测查看 / 报表导出（risk.py）→ risk_prediction_viewed / report_exported 事件

内置事件类型常量与 feature_provider 行为特征聚合的映射约定保持一致。
"""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.behavior_event import BehaviorEvent
from app.models.employee import Employee

logger = get_logger(__name__)

# ===== 内置事件类型（feature_provider 聚合映射依赖这些取值） =====
EVENT_LOGIN = "login"
EVENT_EMAIL = "email"
EVENT_MEETING = "meeting"
EVENT_MEETING_DECLINE = "meeting_decline"
EVENT_WARNING_TRANSITION = "warning_transition"
# 行为事件扩容（feat/rag-kb）：预测查看 / 报表导出（HR 侧操作信号）
EVENT_RISK_PREDICTION_VIEWED = "risk_prediction_viewed"
EVENT_REPORT_EXPORTED = "report_exported"


async def record_behavior_event(
    db: AsyncSession,
    tenant_id: UUID,
    employee_id: UUID,
    event_type: str,
    payload: dict | None = None,
) -> BehaviorEvent | None:
    """记录单条行为事件（best-effort：失败仅告警，绝不阻断调用方业务流程）.

    写入走当前事务（flush 不 commit），由请求级 get_db 统一提交/回滚；
    登录失败等需要立即落库的场景由调用方自行 commit。

    Returns:
        写入成功的 BehaviorEvent；失败时返回 None（异常已内部消化）。
    """
    try:
        event = BehaviorEvent(
            tenant_id=tenant_id,
            employee_id=employee_id,
            event_type=event_type,
            payload=payload if payload is not None else {},
            occurred_at=datetime.now(UTC),
        )
        db.add(event)
        await db.flush()
        return event
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "行为事件写入失败（降级跳过） | event_type=%s | employee_id=%s | err=%s",
            event_type, employee_id, e,
        )
        return None


async def record_behavior_events(
    db: AsyncSession,
    tenant_id: UUID,
    events: list[dict],
) -> int:
    """批量记录行为事件（add_all 单事务；任一条非法则整体放弃，返回已写入条数）.

    参数:
        events: [{"employee_id": UUID, "event_type": str, "payload": dict | None}, ...]
    """
    if not events:
        return 0
    try:
        rows = [
            BehaviorEvent(
                tenant_id=tenant_id,
                employee_id=item["employee_id"],
                event_type=item["event_type"],
                payload=item.get("payload") or {},
                occurred_at=datetime.now(UTC),
            )
            for item in events
        ]
        db.add_all(rows)
        await db.flush()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("行为事件批量写入失败（降级跳过） | tenant_id=%s | n=%s | err=%s",
                       tenant_id, len(events), e)
        return 0


async def resolve_employee_id_by_email(
    db: AsyncSession,
    tenant_id: UUID,
    email: str | None,
) -> UUID | None:
    """按租户内 email 匹配员工 ID（登录账号 → 行为主体，best-effort）."""
    if not email:
        return None
    try:
        stmt = select(Employee.id).where(
            Employee.tenant_id == tenant_id,
            Employee.email == email,
            Employee.deleted_at.is_(None),
        ).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()
    except Exception as e:  # noqa: BLE001
        logger.warning("登录事件员工匹配失败（降级跳过） | tenant_id=%s | err=%s", tenant_id, e)
        return None


async def record_login_event_for_user(db: AsyncSession, user) -> None:
    """登录成功后记 login 行为事件（auth.py 登录流程接线点，best-effort）.

    User 与 Employee 无外键关联：按租户内 email 匹配员工；未匹配到则跳过
    （HR 管理类账号不属于被监测员工的行为信号，不应污染行为特征）。
    """
    try:
        employee_id = await resolve_employee_id_by_email(db, user.tenant_id, user.email)
        if employee_id is None:
            logger.debug("登录事件跳过：无匹配员工 | user_id=%s", user.id)
            return
        await record_behavior_event(
            db=db,
            tenant_id=user.tenant_id,
            employee_id=employee_id,
            event_type=EVENT_LOGIN,
            payload={"user_id": str(user.id), "role": user.role},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("登录行为事件记录失败（降级跳过） | user_id=%s | err=%s", user.id, e)


async def record_warning_transition_event(
    db: AsyncSession,
    warning,
    operator_id: UUID,
    from_status: str,
    to_status: str,
) -> None:
    """预警状态流转后记 warning_transition 行为事件（warnings.py 接线点，best-effort）."""
    await record_behavior_event(
        db=db,
        tenant_id=warning.tenant_id,
        employee_id=warning.employee_id,
        event_type=EVENT_WARNING_TRANSITION,
        payload={
            "warning_id": str(warning.id),
            "level": warning.level,
            "from_status": from_status,
            "to_status": to_status,
            "operator_id": str(operator_id),
        },
    )
