"""管理员路由 - Kill Switch / 漂移检测 / 公平性监测（D03 4.5 + D10 7.3）.

端点：
  GET  /admin/kill-switch           - 查询当前状态
  POST /admin/kill-switch/activate  - 激活（写审计日志）
  POST /admin/kill-switch/deactivate - 解除（写审计日志）
  GET  /admin/drift                 - 漂移检测结果（同步执行检测任务）
  GET  /admin/fairness              - 公平性监测结果（同步执行日报任务）
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core import kill_switch
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.user import ROLE_ADMIN, User
from app.schemas.admin import (
    DriftResult,
    FairnessDimension,
    FairnessResult,
    KillSwitchAction,
    KillSwitchStatus,
)
from app.services.audit_service import append_audit_log
from app.tasks.model_governance import detect_drift, fairness_daily_report

logger = get_logger(__name__)

router = APIRouter()


@router.get("/kill-switch", response_model=KillSwitchStatus)
async def get_kill_switch_status(
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """查询 Kill Switch 当前状态（D03 4.5，仅管理员）."""
    status_dict = await kill_switch.get_status_async()
    return KillSwitchStatus(**status_dict)


@router.post("/kill-switch/activate", response_model=KillSwitchStatus)
async def activate_kill_switch(
    payload: KillSwitchAction,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """激活 Kill Switch（写审计日志）.

    激活后 RiskService.predict 将返回安全降级结果（risk_score=50）。
    """
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="激活原因 reason 不能为空",
        )

    await kill_switch.activate_async(reason=payload.reason, operator_id=str(user.id))

    # 写审计日志
    try:
        await append_audit_log(
            db=db,
            tenant_id=user.tenant_id,
            action="kill_switch.activate",
            resource_type="kill_switch",
            user_id=user.id,
            after_value={"reason": payload.reason, "activated_by": str(user.id)},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill Switch 激活审计日志写入失败 | err=%s", e)

    status_dict = await kill_switch.get_status_async()
    return KillSwitchStatus(**status_dict)


@router.post("/kill-switch/deactivate", response_model=KillSwitchStatus)
async def deactivate_kill_switch(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """解除 Kill Switch（写审计日志）."""
    await kill_switch.deactivate_async(operator_id=str(user.id))

    # 写审计日志
    try:
        await append_audit_log(
            db=db,
            tenant_id=user.tenant_id,
            action="kill_switch.deactivate",
            resource_type="kill_switch",
            user_id=user.id,
            after_value={"deactivated_by": str(user.id)},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Kill Switch 解除审计日志写入失败 | err=%s", e)

    status_dict = await kill_switch.get_status_async()
    return KillSwitchStatus(**status_dict)


@router.get("/drift", response_model=DriftResult)
async def get_model_drift(
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """漂移检测结果（D03 4.5，仅管理员）.

    同步执行 detect_drift 任务逻辑（PSI/KL），数据源不可用时返回 502。
    """
    result = detect_drift()
    if result.get("status") != "ok":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("reason", "漂移检测不可用"),
        )
    return DriftResult(
        max_psi=result["max_psi"],
        critical_features=result.get("critical_features", []),
        features=result.get("features", []),
        computed_at=result["checked_at"],
    )


@router.get("/fairness", response_model=FairnessResult)
async def get_model_fairness(
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """公平性监测结果（D10 7.3，仅管理员）.

    同步执行 fairness_daily_report 任务逻辑（4 维度偏差），
    数据不可用时返回 502；偏差为百分比（0-100）。
    """
    result = fairness_daily_report()
    if result.get("status") != "ok":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("reason", "公平性监测不可用"),
        )
    dimensions = result.get("dimensions", {})
    return FairnessResult(
        dimensions=[
            FairnessDimension(
                name=name,
                label=info.get("label", name),
                disparity=round(float(info["parity_difference"]) * 100, 2),
            )
            for name, info in dimensions.items()
        ],
        computed_at=result["checked_at"],
    )
