"""用户表 ORM - RBAC 5 角色（参考 D04 3.1 users + D03 6.1）."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPKMixin

# RBAC 5 角色（D03 6.1）
ROLE_ADMIN = "admin"           # 管理员（2FA 强制）
ROLE_HR_MANAGER = "hr_manager"  # HR 经理（复核预警）
ROLE_HRBP = "hrbp"             # HRBP（执行干预）
ROLE_MANAGER = "manager"       # 直线经理
ROLE_EMPLOYEE = "employee"     # 员工（自助）

ALL_ROLES = (ROLE_ADMIN, ROLE_HR_MANAGER, ROLE_HRBP, ROLE_MANAGER, ROLE_EMPLOYEE)


class User(Base, UUIDPKMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """用户表 - 登录账号与 RBAC 角色."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True, comment="邮箱（登录账号）")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="bcrypt 哈希")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="用户姓名")
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ROLE_HRBP, comment="角色：admin/hr_manager/hrbp/manager/employee"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", comment="状态：active/disabled"
    )
    totp_secret: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="2FA 密钥（加密）"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
