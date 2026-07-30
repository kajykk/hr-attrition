"""预警 schemas（参考 D05 3.4 + D04 4.3 状态机）."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.warning import (
    LEVEL_P0, LEVEL_P1, LEVEL_P2,
    STATUS_APPEALING, STATUS_CLOSED, STATUS_CONFIRMED,
    STATUS_FIXING, STATUS_NEW, STATUS_REVIEW,
)


class WarningOut(BaseModel):
    """预警输出（D05 3.4 GET /warnings）."""

    id: UUID
    employee_id: UUID
    prediction_id: Optional[UUID] = None
    level: str = Field(description="等级：P0/P1/P2")
    risk_score: int
    status: str = Field(description="状态机当前态")
    assigned_to: Optional[UUID] = None
    escalated_to: Optional[UUID] = None
    message: Optional[str] = None
    created_at: datetime
    confirmed_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WarningStatusUpdate(BaseModel):
    """状态机转换请求（D05 3.4 PATCH /warnings/{id}/status）.

    target_status 取值：new/confirmed/review/fixing/appealing/closed。
    转换合法性由 WarningService.transition 校验，非法转换抛 ValueError。
    """

    target_status: str = Field(
        description="目标状态：confirmed/review/fixing/appealing/closed"
    )
    comment: Optional[str] = Field(default=None, description="备注")
    operator_id: UUID = Field(description="操作人 ID")
    intervention_type: Optional[str] = Field(
        default=None, description="干预类型（fixing 时使用）：raise/transfer/training/coaching/other"
    )
    intervention_description: Optional[str] = None
    appeal_reason: Optional[str] = Field(
        default=None, description="申诉理由：false_alarm/outdated/inaccurate/misleading"
    )
    appeal_description: Optional[str] = None


class WarningEventOut(BaseModel):
    id: UUID
    warning_id: UUID
    action: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    operator_id: UUID
    comment: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedWarnings(BaseModel):
    items: list[WarningOut]
    total: int
    page: int
    page_size: int


# ===== 申诉与标记请求（W4 新增） =====
class AppealRequest(BaseModel):
    """发起申诉请求（POST /warnings/{id}/appeal）."""

    reason: str = Field(description="申诉理由：false_alarm/outdated/inaccurate/misleading 或自定义文本")
    operator_id: UUID = Field(description="操作人 ID（员工或 HR）")
    description: Optional[str] = Field(default=None, description="申诉补充说明")


class MarkRequest(BaseModel):
    """HR 标记请求（POST /warnings/{id}/mark）.

    mark_type 取值：
      - false_positive：误报
      - watching：持续关注
      - communicated：已沟通
    """

    mark_type: str = Field(description="标记类型：false_positive / watching / communicated")
    comment: Optional[str] = Field(default=None, description="备注")
    operator_id: UUID = Field(description="操作人 ID（HR）")


# 合法 mark_type 枚举
ALLOWED_MARK_TYPES = {"false_positive", "watching", "communicated"}
