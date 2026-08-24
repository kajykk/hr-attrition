"""0005: 行为事件表 behavior_events（行为特征基建，README 路线图第一步）.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-24

说明：
  - behavior_events 为行为特征模态的真实数据源：登录（login）、预警状态流转
    （warning_transition）等事件流水，供 feature_provider 近 30 天聚合
  - 租户隔离采用应用层显式过滤模式（ADR-002 现状）：所有查询必须带 tenant_id 条件，
    复合索引 ix_behavior_events_tenant_emp_time 以 tenant_id 打头支撑该过滤
  - id 由 DB 侧 gen_random_uuid() 默认生成（PG13+ 内置），ORM 侧同时保留 uuid4 客户端默认
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "behavior_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="事件 ID（UUID，DB 侧默认生成）",
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="租户 ID（行级隔离 ADR-002，应用层显式过滤）",
        ),
        sa.Column(
            "employee_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="员工 ID（行为主体）",
        ),
        sa.Column(
            "event_type",
            sa.String(64),
            nullable=False,
            comment="事件类型：login/email/meeting/meeting_decline/warning_transition 等",
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="事件发生时间",
        ),
        sa.Column(
            "payload",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="事件负载（自由结构）",
        ),
        comment="行为事件流水（行为特征模态真实数据源）",
    )
    # 复合索引：租户 + 员工 + 时间倒序（近 30 天聚合查询的主路径）
    op.create_index(
        "ix_behavior_events_tenant_emp_time",
        "behavior_events",
        ["tenant_id", "employee_id", sa.text("occurred_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_behavior_events_tenant_emp_time", table_name="behavior_events")
    op.drop_table("behavior_events")
