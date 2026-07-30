"""管理员路由 - Kill Switch 管理（D03 4.5 + PIPL/EU AI Act 合规）.

端点：
  GET  /admin/kill-switch           - 查询当前状态
  POST /admin/kill-switch/activate  - 激活（写审计日志）
  POST /admin/kill-switch/deactivate - 解除（写审计日志）
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import kill_switch
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.user import User
from app.schemas.admin import KillSwitchAction, KillSwitchStatus
from app.services.audit_service import append_audit_log

logger = get_logger(__name__)

router = APIRouter()


@router.get("/kill-switch", response_model=KillSwitchStatus)
async def get_kill_switch_status(
    user: User = Depends(get_current_user),
):
    """查询 Kill Switch 当前状态（D03 4.5）."""
    status_dict = await kill_switch.get_status_async()
    return KillSwitchStatus(**status_dict)


@router.post("/kill-switch/activate", response_model=KillSwitchStatus)
async def activate_kill_switch(
    payload: KillSwitchAction,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
