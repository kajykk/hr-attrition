"""预警 schemas（参考 D05 3.4 + D04 4.3 状态机）."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.warning import (
    LEVEL_P0,
    LEVEL_P1,
    LEVEL_P2,
    STATUS_APPEALING,
    STATUS_CLOSED,
    STATUS_CONFIRMED,
    STATUS_FIXING,
    STATUS_NEW,
    STATUS_REVIEW,
)


class WarningOut(BaseModel):
    """预警输出（D05 3.4 GET /warnings）."""

    id: UUID
    employee_id: UUID
    prediction_id: UUID | None = None
    level: Literal[LEVEL_P0, LEVEL_P1, LEVEL_P2] = Field(description="等级：P0/P1/P2")
    risk_score: int
    status: Literal[
        STATUS_NEW, STATUS_CONFIRMED, STATUS_REVIEW,
        STATUS_FIXING, STATUS_APPEALING, STATUS_CLOSED,
    ] = Field(description="状态机当前态")
    assigned_to: UUID | None = None
    escalated_to: UUID | None = None
    message: str | None = None
    created_at: datetime
    confirmed_at: datetime | None = None
    closed_at: datetime | None = None

    model_config = {"from_attributes": True}


class WarningStatusUpdate(BaseModel):
    """状态机转换请求（D05 3.4 PATCH /warnings/{id}/status）.

    target_status 取值：new/confirmed/review/fixing/appealing/closed。
    转换合法性由 WarningService.transition 校验，非法转换抛 ValueError。
    """

    target_status: Literal[
        STATUS_NEW, STATUS_CONFIRMED, STATUS_REVIEW,
        STATUS_FIXING, STATUS_APPEALING, STATUS_CLOSED,
    ] = Field(
        description="目标状态：confirmed/review/fixing/appealing/closed"
    )
    comment: str | None = Field(default=None, description="备注")
    operator_id: UUID | None = Field(
        default=None,
        description="已弃用：操作人由服务端从认证 token 派生，客户端无需提供",
    )
    intervention_type: Literal["raise", "transfer", "training", "coaching", "other"] | None = Field(
        default=None, description="干预类型（fixing 时使用）：raise/transfer/training/coaching/other"
    )
    intervention_description: str | None = None
    appeal_reason: Literal["false_alarm", "outdated", "inaccurate", "misleading"] | None = Field(
        default=None, description="申诉理由：false_alarm/outdated/inaccurate/misleading"
    )
    appeal_description: str | None = None


class WarningEventOut(BaseModel):
    id: UUID
    warning_id: UUID
    action: str
    from_status: str | None = None
    to_status: str | None = None
    operator_id: UUID
    comment: str | None = None
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

    reason: str = Field(
        min_length=2,
        max_length=200,
        description="申诉理由：false_alarm/outdated/inaccurate/misleading 或自定义文本",
    )
    operator_id: UUID | None = Field(
        default=None,
        description="已弃用：操作人由服务端从认证 token 派生，客户端无需提供",
    )
    description: str | None = Field(default=None, description="申诉补充说明")


class MarkRequest(BaseModel):
    """HR 标记请求（POST /warnings/{id}/mark）.

    mark_type 取值：
      - false_positive：误报
      - watching：持续关注
      - communicated：已沟通
    """

    mark_type: Literal["false_positive", "watching", "communicated"] = Field(
        description="标记类型：false_positive / watching / communicated"
    )
    comment: str | None = Field(default=None, description="备注")
    operator_id: UUID | None = Field(
        default=None,
        description="已弃用：操作人由服务端从认证 token 派生，客户端无需提供",
    )


# 合法 mark_type 枚举
ALLOWED_MARK_TYPES = {"false_positive", "watching", "communicated"}
