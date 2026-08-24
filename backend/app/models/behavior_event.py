"""行为事件表 ORM - 行为特征基建（README 路线图第一步）.

行为事件流水是行为特征模态的真实数据源：
  - 登录成功（auth.py）记 login 事件
  - 预警状态流转（warnings.py / warning_service）记 warning_transition 事件
feature_provider 近 30 天聚合各 event_type 计数构造行为特征（真实模式），
无数据/不足阈值时回退 demo 模式并以 behavior_data_source 标注。

租户隔离：应用层显式过滤模式（ADR-002 现状），所有查询必须带 tenant_id 条件。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPKMixin


class BehaviorEvent(Base, UUIDPKMixin, TenantMixin):
    """行为事件流水表 - 行为特征模态的真实数据源."""

    __tablename__ = "behavior_events"
    __table_args__ = (
        # 与迁移 0005 一致：租户 + 员工 + 时间倒序（近 30 天聚合主路径）
        Index(
            "ix_behavior_events_tenant_emp_time",
            "tenant_id",
            "employee_id",
            text("occurred_at DESC"),
        ),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, comment="员工 ID（行为主体）"
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="事件类型：login/email/meeting/meeting_decline/warning_transition 等",
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="事件发生时间",
    )
    payload: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
        comment="事件负载（自由结构）",
    )
