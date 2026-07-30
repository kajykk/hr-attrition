"""员工表 ORM - 含 6 个 PII Fernet 加密字段 + 哈希检索字段（参考 D04 3.1 + V1.1）.

PII 加密清单（ADR-007，季度轮换）：
  - name_encrypted        姓名
  - id_card_encrypted     身份证号
  - phone_encrypted       手机号
  - salary_encrypted      薪资绝对值
  - ethnicity_encrypted   民族（V1.1 新增，仅公平性审计，模型禁用，单独同意）
  - disability_encrypted  残疾状况（V1.1 新增，仅公平性审计，模型禁用，单独同意）

注意：加密字段不可直接查询，配套 name_hash/ethnicity_hash/disability_hash 用于 SHA256 检索。
性别/民族/残疾/出生日期字段仅用于公平性审计，模型推理严禁使用（fairness_monitor 监控）。
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPKMixin


class Employee(Base, UUIDPKMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """员工表 - 业务核心实体."""

    __tablename__ = "employees"

    employee_no: Mapped[str] = mapped_column(String(50), nullable=False, index=True, comment="工号（租户内唯一）")

    # ===== PII 加密字段（Fernet 对称加密，ADR-007） =====
    name_encrypted: Mapped[str] = mapped_column(String, nullable=False, comment="姓名（Fernet 加密）")
    name_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="姓名 SHA256（检索用）")
    id_card_encrypted: Mapped[str | None] = mapped_column(String, nullable=True, comment="身份证号（Fernet）")
    phone_encrypted: Mapped[str | None] = mapped_column(String, nullable=True, comment="手机号（Fernet）")
    salary_encrypted: Mapped[str | None] = mapped_column(String, nullable=True, comment="薪资绝对值（Fernet）")

    # ===== V1.1 新增：公平性审计 PII 字段（仅审计，模型禁用，单独同意） =====
    ethnicity_encrypted: Mapped[str | None] = mapped_column(
        String, nullable=True, comment="民族（Fernet 加密，仅公平性审计，模型禁用，单独同意）"
    )
    ethnicity_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="民族 SHA256（检索用）"
    )
    disability_encrypted: Mapped[str | None] = mapped_column(
        String, nullable=True, comment="残疾状况（Fernet 加密，仅公平性审计，模型禁用，单独同意）"
    )
    disability_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="残疾状况 SHA256（检索用）"
    )

    # ===== 非加密字段 =====
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gender: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="性别（仅用于公平性审计，模型禁用）"
    )
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="出生日期（年龄公平性审计）")
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True, index=True
    )
    position: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="岗位")
    level: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="职级")
    hire_date: Mapped[date] = mapped_column(Date, nullable=False, comment="入职日期")
    salary_percentile: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True, comment="薪资分位（0-100）"
    )

    # ===== 状态 =====
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", comment="在职/离职/试用期"
    )
    leave_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="离职日期")
    leave_reason: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="离职原因")

    # ===== 同意（PIPL） =====
    consent_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", comment="同意状态"
    )
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="同意时间")
