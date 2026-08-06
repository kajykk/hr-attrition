"""add real feature fields to employees

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-06

P0-4 特征真实化：为 employees 表新增 20 个模型特征真实字段（推理侧
真实值优先、缺失时用训练分布中位占位，替代原先确定性伪随机生成）。
全部 nullable，向后兼容存量数据。
"""
import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("distance_from_home", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("education", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("environment_satisfaction", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("job_involvement", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("job_level", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("job_satisfaction", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("num_companies_worked", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("percent_salary_hike", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("performance_rating", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("relationship_satisfaction", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("stock_option_level", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("total_working_years", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("training_times_last_year", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("work_life_balance", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("years_in_current_role", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("years_since_last_promotion", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("years_with_curr_manager", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("overtime", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("business_travel", sa.String(length=20), nullable=True))
    op.add_column("employees", sa.Column("marital_status", sa.String(length=20), nullable=True))


def downgrade() -> None:
    for col in (
        "marital_status", "business_travel", "overtime",
        "years_with_curr_manager", "years_since_last_promotion", "years_in_current_role",
        "work_life_balance", "training_times_last_year", "total_working_years",
        "stock_option_level", "relationship_satisfaction", "performance_rating",
        "percent_salary_hike", "num_companies_worked", "job_satisfaction", "job_level",
        "job_involvement", "environment_satisfaction", "education", "distance_from_home",
    ):
        op.drop_column("employees", col)
