"""0004: users 表新增 locked_until 登录锁定字段（防爆破加固）.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-24

说明：
  - 连续失败 ≥ LOGIN_MAX_FAILED_ATTEMPTS（默认 5）次 → locked_until = now + 15min
  - nullable，向后兼容存量数据（无锁语义）
"""
import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True,
                  comment="登录连续失败锁定截止时间"),
    )


def downgrade() -> None:
    op.drop_column("users", "locked_until")
