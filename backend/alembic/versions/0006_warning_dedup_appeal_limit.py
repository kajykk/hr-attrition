"""0006: 预警防重复建警 + 申诉次数上限（状态机五项空缺补齐 1/4）.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-25

说明：
  - warnings.appeal_count：已发起申诉次数计数（默认 0），
    appealing 入口 >= 3 次拒绝（409「申诉次数已达上限」）
  - uq_warnings_active_tenant_emp_level：部分唯一索引，
    同租户同员工同等级仅允许一条未关闭（new/confirmed/review/fixing/appealing）预警；
    应用层 create_warning 先查后插 + 冲突 retry 一次，DB 层该索引兜底并发窗口
    （按现有表结构适配：active 状态集为 new/confirmed/review/fixing/appealing，
    粒度含 level，允许 P0/P1/P2 各存一条在办预警）
"""
import sqlalchemy as sa

from alembic import op
from app.models.warning import UQ_ACTIVE_WARNINGS_INDEX

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "warnings",
        sa.Column(
            "appeal_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="已发起申诉次数（>= 3 后 appealing 入口拒绝）",
        ),
    )
    op.create_index(
        UQ_ACTIVE_WARNINGS_INDEX,
        "warnings",
        ["tenant_id", "employee_id", "level"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('new', 'confirmed', 'review', 'fixing', 'appealing')"
        ),
    )


def downgrade() -> None:
    op.drop_index(UQ_ACTIVE_WARNINGS_INDEX, table_name="warnings")
    op.drop_column("warnings", "appeal_count")
