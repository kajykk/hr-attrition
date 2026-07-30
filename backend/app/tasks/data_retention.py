"""数据保留与物理删除任务 - PIPL/欧盟 AI Act 合规（D11 C-COMP-06）.

任务（Celery beat 调度）：
  - purge_departed_employees()：每周日 04:00，物理删除离职满 2 年的员工及其关联数据

删除策略（D04 5.2 + D11 C-COMP-05/06）：
  - risk_predictions：分区保留 2 年 → 物理删除
  - warnings / warning_events：申诉相关保留 5 年 → 仅删除离职 ≥ 2 年且无未结申诉的员工
  - audit_logs：保留 5 年（含哈希链），**不删除**（其中无直接 PII，仅含 user_id/resource_id UUID）
  - employees：物理删除（含 6 个 PII 加密字段）

降级策略：
  - DB 不可用 → 返回 {status: "skipped", reason: ...}
  - 单租户失败不阻塞其他租户
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select

from app.celery_app import celery_app
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.risk_prediction import RiskPrediction
from app.models.warning import WarningEvent, WarningRecord
from app.services.audit_service import append_audit_log

logger = get_logger(__name__)

# 离职满 2 年物理删除（PIPL 第 47 条 / D11 C-COMP-06）
RETENTION_YEARS_AFTER_LEAVE = 2

# 申诉相关保留 5 年（D11 C-COMP-05）
RETENTION_YEARS_WARNINGS = 5

# 未结申诉状态不予删除（避免法律举证需要）
_UNCLOSED_WARNING_STATUSES = ("new", "confirmed", "review", "fixing", "appealing")


async def _list_purge_candidates(tenant_id: UUID | None = None) -> list[Employee]:
    """查询符合物理删除条件的员工列表.

    条件：
      - status='left'（已离职）
      - leave_date <= today - 2 年
      - 该员工无未结申诉预警（warnings.status 不在 _UNCLOSED_WARNING_STATUSES）
    """
    cutoff_date = date.today() - timedelta(days=RETENTION_YEARS_AFTER_LEAVE * 365)

    stmt = (
        select(Employee)
        .where(
            Employee.status == "left",
            Employee.leave_date.is_not(None),
            Employee.leave_date <= cutoff_date,
        )
    )
    if tenant_id is not None:
        stmt = stmt.where(Employee.tenant_id == tenant_id)

    async with async_session_factory() as session:
        result = await session.execute(stmt)
        candidates = list(result.scalars().all())

    # 过滤：仍有未结申诉的员工不删除
    if not candidates:
        return []

    candidate_ids = [emp.id for emp in candidates]
    async with async_session_factory() as session:
        unclosed_stmt = (
            select(WarningRecord.employee_id)
            .where(
                WarningRecord.employee_id.in_(candidate_ids),
                WarningRecord.status.in_(_UNCLOSED_WARNING_STATUSES),
            )
            .distinct()
        )
        unclosed_result = await session.execute(unclosed_stmt)
        unclosed_employee_ids = {row[0] for row in unclosed_result.all()}

    return [emp for emp in candidates if emp.id not in unclosed_employee_ids]


async def _purge_employee_data(employee: Employee) -> dict:
    """物理删除单个员工的全部业务数据（保留 audit_logs 5 年哈希链）.

    删除顺序（外键约束）：
      1. warning_events（引用 warnings）
      2. warnings
      3. risk_predictions
      4. employees
      5. 写入 audit_log：action=employee.purged
    """
    emp_id = employee.id
    tenant_id = employee.tenant_id
    deleted = {
        "employee_id": str(emp_id),
        "tenant_id": str(tenant_id),
        "warning_events": 0,
        "warnings": 0,
        "risk_predictions": 0,
        "employee": 0,
    }

    async with async_session_factory() as session:
        try:
            # 1. 收集该员工预警 ID
            warning_ids_stmt = select(WarningRecord.id).where(WarningRecord.employee_id == emp_id)
            warning_ids_result = await session.execute(warning_ids_stmt)
            warning_ids = [row[0] for row in warning_ids_result.all()]

            # 2. 删除 warning_events
            if warning_ids:
                we_result = await session.execute(
                    delete(WarningEvent).where(WarningEvent.warning_id.in_(warning_ids))
                )
                deleted["warning_events"] = we_result.rowcount or 0

            # 3. 删除 warnings
            w_result = await session.execute(
                delete(WarningRecord).where(WarningRecord.employee_id == emp_id)
            )
            deleted["warnings"] = w_result.rowcount or 0

            # 4. 删除 risk_predictions
            rp_result = await session.execute(
                delete(RiskPrediction).where(RiskPrediction.employee_id == emp_id)
            )
            deleted["risk_predictions"] = rp_result.rowcount or 0

            # 5. 删除 employees
            emp_result = await session.execute(
                delete(Employee).where(Employee.id == emp_id)
            )
            deleted["employee"] = emp_result.rowcount or 0

            # 6. 写审计日志（保留 5 年，证明物理删除发生过）
            await append_audit_log(
                db=session,
                tenant_id=tenant_id,
                action="employee.purged",
                resource_type="employee",
                resource_id=emp_id,
                after_value=deleted,
            )

            await session.commit()
            return deleted
        except Exception:
            await session.rollback()
            raise


@celery_app.task(name="app.tasks.data_retention.purge_departed_employees")
def purge_departed_employees(tenant_id: str | None = None) -> dict:
    """物理删除离职满 2 年的员工（每周日 04:00，PIPL 第 47 条）.

    Args:
        tenant_id: 可选，限定单租户执行（用于手动触发或演练）。

    Returns:
        {
            status: "ok" | "skipped",
            purged_count: N,
            details: [...],
            skipped_unclosed: M,  # 因未结申诉跳过
            checked_at: ISO8601,
        }
    """
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info("数据保留清理任务执行 | tenant=%s | time=%s", tenant_id or "ALL", started_at)

    try:
        target_tenant = UUID(tenant_id) if tenant_id else None
    except ValueError:
        return {
            "status": "skipped",
            "reason": f"tenant_id 非法: {tenant_id}",
            "checked_at": started_at,
        }

    # 候选查询
    try:
        candidates = asyncio.run(_list_purge_candidates(target_tenant))
    except Exception as e:  # noqa: BLE001
        logger.error("数据保留清理：候选查询失败 | err=%s", e)
        return {
            "status": "skipped",
            "reason": f"DB 不可用: {e}",
            "checked_at": started_at,
        }

    if not candidates:
        logger.info("数据保留清理：无候选员工")
        return {
            "status": "ok",
            "purged_count": 0,
            "details": [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    details: list[dict] = []
    failures: list[dict] = []
    for emp in candidates:
        try:
            result = asyncio.run(_purge_employee_data(emp))
            details.append(result)
        except Exception as e:  # noqa: BLE001
            logger.error("员工物理删除失败 | emp_id=%s | err=%s", emp.id, e)
            failures.append({"employee_id": str(emp.id), "error": str(e)})

    summary = {
        "status": "ok" if not failures else "partial",
        "purged_count": len(details),
        "failed_count": len(failures),
        "details": details,
        "failures": failures,
        "retention_years": RETENTION_YEARS_AFTER_LEAVE,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "数据保留清理完成 | purged=%d | failed=%d",
        summary["purged_count"], summary["failed_count"],
    )
    return summary


@celery_app.task(name="app.tasks.data_retention.report_retention_status")
def report_retention_status() -> dict:
    """数据保留状态报告（每月 1 号 05:00，供 D11 C-COMP 验收）.

    统计：
      - 已离职未满 2 年（仍在保留期内）
      - 已离职满 2 年待删除
      - 已离职满 2 年但有未结申诉（不删除）
      - audit_logs 5 年内总量
    """
    cutoff_date = date.today() - timedelta(days=RETENTION_YEARS_AFTER_LEAVE * 365)
    audit_cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_YEARS_WARNINGS * 365)

    async def _collect() -> dict:
        async with async_session_factory() as session:
            in_retention = await session.execute(
                select(func.count(Employee.id)).where(
                    Employee.status == "left",
                    Employee.leave_date.is_not(None),
                    Employee.leave_date > cutoff_date,
                )
            )
            pending_purge = await session.execute(
                select(func.count(Employee.id)).where(
                    Employee.status == "left",
                    Employee.leave_date.is_not(None),
                    Employee.leave_date <= cutoff_date,
                )
            )
            unclosed_warnings = await session.execute(
                select(func.count(WarningRecord.id)).where(
                    WarningRecord.status.in_(_UNCLOSED_WARNING_STATUSES)
                )
            )
            audit_logs_in_retention = await session.execute(
                select(func.count(AuditLog.id)).where(AuditLog.created_at >= audit_cutoff)
            )
            return {
                "in_retention_employees": in_retention.scalar() or 0,
                "pending_purge_employees": pending_purge.scalar() or 0,
                "unclosed_warnings": unclosed_warnings.scalar() or 0,
                "audit_logs_in_retention": audit_logs_in_retention.scalar() or 0,
            }

    try:
        stats = asyncio.run(_collect())
    except Exception as e:  # noqa: BLE001
        logger.error("数据保留状态报告失败 | err=%s", e)
        return {
            "status": "skipped",
            "reason": f"DB 不可用: {e}",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "status": "ok",
        **stats,
        "retention_policy": {
            "employees_years": RETENTION_YEARS_AFTER_LEAVE,
            "audit_logs_years": RETENTION_YEARS_WARNINGS,
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
