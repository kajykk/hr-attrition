"""行为事件上报 schemas（前端埋点 → API → behavior_events 表）.

前端页面级/功能级事件经此结构上报；事件是否落库由服务端按
「能否解析到员工主体」决定（与登录事件同一 best-effort 语义），
不匹配员工的事件静默丢弃（不污染行为特征）。
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# 前端埋点事件类型（ui_ 前缀区分于后端内置事件）
EVENT_UI_PAGE_VIEW = "ui_page_view"
EVENT_UI_FEATURE_USE = "ui_feature_use"
EVENT_UI_REPORT_VIEW = "ui_report_view"

# 服务端接受的前端事件白名单（防任意事件类型灌入）
UI_EVENT_TYPES = Literal[
    "ui_page_view",
    "ui_feature_use",
    "ui_report_view",
]


class BehaviorEventIn(BaseModel):
    """单条前端行为事件上报."""

    event_type: str = Field(min_length=1, max_length=64, description="事件类型")
    employee_id: UUID | None = Field(
        default=None,
        description="员工主体（页面级事件可省略；省略时服务端尝试按当前用户匹配）",
    )
    payload: dict = Field(
        default_factory=dict, description="事件负载（自由结构，将被 JSONB 序列化）"
    )
    occurred_at: datetime | None = Field(
        default=None, description="事件发生时间（缺省为服务端接收时刻）"
    )


class BehaviorEventBatchIn(BaseModel):
    """批量上报：单请求 ≤ 50 条，超出丢弃并记录告警."""

    events: list[BehaviorEventIn] = Field(
        min_length=1, max_length=50, description="事件列表（1-50 条）"
    )
