"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-01

HRA 初始 schema（8 张表）：
  tenants / users / departments / employees /
  risk_predictions / warnings / warning_events / audit_logs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # ===== tenants =====
    op.create_table(
        "tenants",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False, unique=True),
        sa.Column("plan", sa.String(length=20), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("encryption_key_id", _uuid(), nullable=True),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default="{}",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ===== users =====
    op.create_table(
        "users",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="hrbp"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("totp_secret", sa.String(length=255), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip", postgresql.INET(), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # ===== departments =====
    op.create_table(
        "departments",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("parent_id", _uuid(), nullable=True),
        sa.Column("manager_id", _uuid(), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("headcount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["parent_id"], ["departments.id"]),
    )
    op.create_index("ix_departments_tenant_id", "departments", ["tenant_id"])
    op.create_index("ix_departments_parent_id", "departments", ["parent_id"])

    # ===== employees =====
    op.create_table(
        "employees",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("employee_no", sa.String(length=50), nullable=False),
        sa.Column("name_encrypted", sa.Text(), nullable=False),
        sa.Column("name_hash", sa.String(length=64), nullable=False),
        sa.Column("id_card_encrypted", sa.Text(), nullable=True),
        sa.Column("phone_encrypted", sa.Text(), nullable=True),
        sa.Column("salary_encrypted", sa.Text(), nullable=True),
        sa.Column("ethnicity_encrypted", sa.Text(), nullable=True),
        sa.Column("ethnicity_hash", sa.String(length=64), nullable=True),
        sa.Column("disability_encrypted", sa.Text(), nullable=True),
        sa.Column("disability_hash", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("gender", sa.String(length=10), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("department_id", _uuid(), nullable=True),
        sa.Column("position", sa.String(length=100), nullable=True),
        sa.Column("level", sa.String(length=20), nullable=True),
        sa.Column("hire_date", sa.Date(), nullable=False),
        sa.Column("salary_percentile", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("leave_date", sa.Date(), nullable=True),
        sa.Column("leave_reason", sa.String(length=100), nullable=True),
        sa.Column("consent_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.UniqueConstraint("employee_no", "tenant_id", name="uq_employees_no_tenant"),
    )
    op.create_index("ix_employees_tenant_id", "employees", ["tenant_id"])
    op.create_index("ix_employees_employee_no", "employees", ["employee_no"])
    op.create_index("ix_employees_name_hash", "employees", ["name_hash"])
    op.create_index("ix_employees_ethnicity_hash", "employees", ["ethnicity_hash"])
    op.create_index("ix_employees_disability_hash", "employees", ["disability_hash"])
    op.create_index("ix_employees_department_id", "employees", ["department_id"])

    # ===== risk_predictions =====
    op.create_table(
        "risk_predictions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("employee_id", _uuid(), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column(
            "modality_scores", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default="{}",
        ),
        sa.Column(
            "feature_values", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default="{}",
        ),
        sa.Column("predicted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
    )
    op.create_index("ix_risk_predictions_tenant_id", "risk_predictions", ["tenant_id"])
    op.create_index("ix_risk_predictions_employee_id", "risk_predictions", ["employee_id"])
    op.create_index("ix_risk_predictions_predicted_at", "risk_predictions", ["predicted_at"])
    op.create_index("ix_risk_predictions_batch_id", "risk_predictions", ["batch_id"])
    op.create_index(
        "ix_risk_predictions_tenant_predicted",
        "risk_predictions", ["tenant_id", "predicted_at"],
    )

    # ===== warnings =====
    op.create_table(
        "warnings",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("employee_id", _uuid(), nullable=False),
        sa.Column("prediction_id", _uuid(), nullable=True),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column("assigned_to", _uuid(), nullable=True),
        sa.Column("escalated_to", _uuid(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
    )
    op.create_index("ix_warnings_tenant_id", "warnings", ["tenant_id"])
    op.create_index("ix_warnings_employee_id", "warnings", ["employee_id"])
    op.create_index("ix_warnings_level", "warnings", ["level"])
    op.create_index("ix_warnings_status", "warnings", ["status"])
    op.create_index("ix_warnings_assigned_to", "warnings", ["assigned_to"])
    op.create_index("ix_warnings_created_at", "warnings", ["created_at"])
    op.create_index("ix_warnings_tenant_status", "warnings", ["tenant_id", "status"])

    # ===== warning_events =====
    op.create_table(
        "warning_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("warning_id", _uuid(), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("operator_id", _uuid(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["warning_id"], ["warnings.id"]),
    )
    op.create_index("ix_warning_events_tenant_id", "warning_events", ["tenant_id"])
    op.create_index("ix_warning_events_warning_id", "warning_events", ["warning_id"])
    op.create_index("ix_warning_events_created_at", "warning_events", ["created_at"])

    # ===== audit_logs =====
    op.create_table(
        "audit_logs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("tenant_id", _uuid(), nullable=False),
        sa.Column("user_id", _uuid(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", _uuid(), nullable=True),
        sa.Column("before_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("current_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_resource_id", "audit_logs", ["resource_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("warning_events")
    op.drop_table("warnings")
    op.drop_table("risk_predictions")
    op.drop_table("employees")
    op.drop_table("departments")
    op.drop_table("users")
    op.drop_table("tenants")
