"""行为事件上报路由 - 前端埋点管道入口.

管道：前端埋点 → POST /api/v1/behavior/events → behavior_events 表 → 行为模态特征

语义：
  - 事件必须能解析到员工主体才会落库（best-effort，与登录事件一致）：
    显式 employee_id 优先；否则按当前登录用户 email 匹配租户内员工，
    未匹配（如 HR 管理账号）则丢弃该事件。
  - 页面级事件（ui_page_view）由前端在路由切换/可见性变化时上报；
    功能级事件（ui_feature_use）在关键交互触发时上报。
  - 全部 best-effort：上报失败不影响业务请求，限流 429 静默降级。
"""
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.behavior import BehaviorEventBatchIn
from app.services.behavior_service import (
    record_behavior_events,
    resolve_employee_id_by_email,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 前端事件限流：单租户滑动窗口（60 秒）内最多 300 条。
# 内存实现（无 Redis 依赖），进程内窗口计数，重启清零——best-effort 管道可接受。
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_EVENTS = 300

# tenant_id → (window_start_ts, count)；测试可经 _reset_rate_limiter 重置
_rate_windows: dict[str, tuple[int, int]] = {}


def _reset_rate_limiter() -> None:
    """清空限流窗口（测试用）."""
    _rate_windows.clear()


def _check_rate_limit(tenant_id: str, n: int) -> None:
    now = int(time.time())
    window_start, count = _rate_windows.get(tenant_id, (now, 0))
    if now - window_start >= _RATE_WINDOW_SECONDS:
        window_start, count = now, 0
    count += n
    _rate_windows[tenant_id] = (window_start, count)
    if count > _RATE_MAX_EVENTS:
        logger.warning(
            "行为事件上报超限（丢弃） | tenant_id=%s | n=%s", tenant_id, n,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="行为事件上报过于频繁，请稍后重试",
        )


@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
    summary="批量上报前端行为事件（埋点管道）",
)
async def record_frontend_events(
    body: BehaviorEventBatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """接收前端埋点批量事件，解析员工主体后写入 behavior_events.

    返回 202 即视为已受理；`accepted` 为实际写入条数（无员工主体的事件被丢弃）。
    """
    tenant_id = user.tenant_id

    # 限流（内存滑动窗口，超限抛 429）
    _check_rate_limit(str(tenant_id), len(body.events))

    accepted = 0
    try:
        # 事件 → 员工主体解析（显式 employee_id 或按当前用户 email 匹配）
        rows: list[dict] = []
        for ev in body.events:
            employee_id = ev.employee_id
            if employee_id is None:
                employee_id = await resolve_employee_id_by_email(db, tenant_id, user.email)
            if employee_id is None:
                continue  # 无员工主体（管理账号等）：丢弃，不污染行为特征
            rows.append(
                {
                    "employee_id": employee_id,
                    "event_type": ev.event_type,
                    "payload": ev.payload,
                }
            )
        if rows:
            accepted = await record_behavior_events(db=db, tenant_id=tenant_id, events=rows)
    except HTTPException:
        raise
    except Exception:
        logger.exception("行为事件上报处理异常 | tenant_id=%s", tenant_id)
        accepted = 0

    return {"accepted": accepted, "received": len(body.events)}
