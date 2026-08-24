"""预警路由（D05 3.4 + D04 4.3 状态机转换 + W4 申诉/标记）."""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.logging import get_logger
from app.core.tenant import get_current_tenant_id
from app.db.session import get_db
from app.models.user import (
    ROLE_ADMIN,
    ROLE_HR_MANAGER,
    ROLE_HRBP,
    User,
)
from app.models.warning import WarningEvent, WarningRecord
from app.schemas.warning import (
    ALLOWED_MARK_TYPES,
    AppealRequest,
    MarkRequest,
    PaginatedWarnings,
    WarningOut,
    WarningStatusUpdate,
)
from app.services.audit_service import append_audit_log
from app.services.warning_service import WarningService

router = APIRouter()
logger = get_logger(__name__)

# 预警处理角色（状态转换/标记）：HR 经理 / HRBP / 管理员
_HR_ROLES = (ROLE_ADMIN, ROLE_HR_MANAGER, ROLE_HRBP)


async def _log_warning_audit(
    db: AsyncSession,
    tenant_id,
    action: str,
    warning: WarningRecord,
    operator_id,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """写预警操作审计（best-effort，失败不阻断业务）."""
    try:
        await append_audit_log(
            db=db,
            tenant_id=tenant_id,
            action=action,
            resource_type="warning",
            resource_id=warning.id,
            user_id=operator_id,
            before_value=before,
            after_value=after,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("预警审计日志写入失败 | action=%s | warning_id=%s | err=%s",
                       action, warning.id, e)


@router.get("", response_model=PaginatedWarnings)
async def list_warnings(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    level: str | None = Query(None),
    assigned_to: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """预警列表（D05 3.4 GET /warnings，多维度筛选 + 租户隔离）."""
    tenant_id = get_current_tenant_id()

    stmt = select(WarningRecord).where(WarningRecord.tenant_id == tenant_id)
    if status_filter:
        stmt = stmt.where(WarningRecord.status == status_filter)
    if level:
        stmt = stmt.where(WarningRecord.level == level)
    if assigned_to:
        stmt = stmt.where(WarningRecord.assigned_to == assigned_to)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(WarningRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return PaginatedWarnings(
        items=[WarningOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{warning_id}", response_model=WarningOut)
async def get_warning(
    warning_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """预警详情（D05 3.4 GET /warnings/{id}）."""
    tenant_id = get_current_tenant_id()
    stmt = select(WarningRecord).where(
        WarningRecord.id == warning_id,
        WarningRecord.tenant_id == tenant_id,
    )
    w = (await db.execute(stmt)).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="预警不存在")
    return WarningOut.model_validate(w)


@router.patch("/{warning_id}/status", response_model=WarningOut)
async def update_warning_status(
    warning_id: UUID,
    payload: WarningStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_HR_ROLES)),
):
    """状态机转换（D05 3.4 PATCH /warnings/{id}/status + D04 4.3）.

    仅 HR 角色（admin/hr_manager/hrbp）可执行；操作人取自认证用户而非客户端。
    转换合法性由 WarningService.transition 校验：
      - P0：confirmed → review → fixing（FR-LOOP-004 强制复核）
      - P1/P2：confirmed → fixing（直转）
      - 非法转换抛 ValueError，API 返回 422
    """
    tenant_id = get_current_tenant_id()
    stmt = select(WarningRecord).where(
        WarningRecord.id == warning_id,
        WarningRecord.tenant_id == tenant_id,
    )
    w = (await db.execute(stmt)).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="预警不存在")

    operator_id = user.id  # 操作人从认证用户派生（防审计伪造）
    try:
        from_status, to_status = WarningService.transition(
            warning=w,
            target_status=payload.target_status,
            operator_id=operator_id,
            comment=payload.comment,
        )
    except ValueError as e:
        # 非法状态转换（约束 7）
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # 记录事件（审计追溯）
    event = WarningEvent(
        tenant_id=tenant_id,
        warning_id=w.id,
        action=to_status,
        from_status=from_status,
        to_status=to_status,
        operator_id=operator_id,
        comment=payload.comment,
        created_at=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    # 行为特征基建（README 路线图第一步）：状态流转记 warning_transition 事件
    # （best-effort，内部失败降级跳过，不阻断预警处理）
    from app.services.behavior_service import record_warning_transition_event
    await record_warning_transition_event(db, w, operator_id, from_status, to_status)
    await db.refresh(w)

    # 审计日志（P2-10）
    await _log_warning_audit(
        db, tenant_id, "warning.transition", w, operator_id,
        before={"status": from_status},
        after={"status": to_status, "comment": payload.comment},
    )

    return WarningOut.model_validate(w)


# ===== W4 新增：申诉与标记端点 =====


@router.post("/{warning_id}/appeal", response_model=WarningOut)
async def appeal_warning(
    warning_id: UUID,
    payload: AppealRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发起申诉（POST /warnings/{id}/appeal）.

    校验当前状态允许转 appealing（用 WarningService.validate_transition），
    复用 PATCH /status 的逻辑（target_status="appealing"）。
    非法转换（如 closed → appealing）返回 422。
    """
    tenant_id = get_current_tenant_id()
    stmt = select(WarningRecord).where(
        WarningRecord.id == warning_id,
        WarningRecord.tenant_id == tenant_id,
    )
    w = (await db.execute(stmt)).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="预警不存在")

    # 拼接申诉理由作为备注
    comment = f"[申诉] {payload.reason}"
    if payload.description:
        comment += f" | {payload.description}"

    operator_id = user.id  # 操作人从认证用户派生（防审计伪造）
    try:
        from_status, to_status = WarningService.transition(
            warning=w,
            target_status="appealing",
            operator_id=operator_id,
            comment=comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # 记录事件（action=appealing，审计追溯）
    event = WarningEvent(
        tenant_id=tenant_id,
        warning_id=w.id,
        action="appealing",
        from_status=from_status,
        to_status=to_status,
        operator_id=operator_id,
        comment=comment,
        created_at=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    # 行为特征基建：申诉也是状态流转，记 warning_transition 事件（best-effort）
    from app.services.behavior_service import record_warning_transition_event
    await record_warning_transition_event(db, w, operator_id, from_status, to_status)
    await db.refresh(w)

    # 审计日志（P2-10）
    await _log_warning_audit(
        db, tenant_id, "warning.appeal", w, operator_id,
        before={"status": from_status},
        after={"status": to_status, "reason": payload.reason},
    )

    return WarningOut.model_validate(w)


@router.post("/{warning_id}/mark", response_model=WarningOut)
async def mark_warning(
    warning_id: UUID,
    payload: MarkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_HR_ROLES)),
):
    """HR 标记预警（POST /warnings/{id}/mark）.

    mark_type 取值：false_positive（误报）/ watching（关注）/ communicated（已沟通）。
    不改变状态机，仅写入 WarningEvent（action=mark）作为审计标记。
    """
    # 校验 mark_type
    if payload.mark_type not in ALLOWED_MARK_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"非法 mark_type：{payload.mark_type}（合法值：{sorted(ALLOWED_MARK_TYPES)}）",
        )

    tenant_id = get_current_tenant_id()
    stmt = select(WarningRecord).where(
        WarningRecord.id == warning_id,
        WarningRecord.tenant_id == tenant_id,
    )
    w = (await db.execute(stmt)).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="预警不存在")

    # 写入标记事件（不改变状态，仅审计记录）
    comment = f"[标记:{payload.mark_type}] {payload.comment or ''}"
    event = WarningEvent(
        tenant_id=tenant_id,
        warning_id=w.id,
        action="mark",
        from_status=w.status,
        to_status=w.status,  # 状态不变
        operator_id=user.id,  # 操作人从认证用户派生（防审计伪造）
        comment=comment,
        created_at=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    await db.refresh(w)

    # 审计日志（P2-10）
    await _log_warning_audit(
        db, tenant_id, "warning.mark", w, user.id,
        after={"mark_type": payload.mark_type, "comment": payload.comment},
    )

    return WarningOut.model_validate(w)
