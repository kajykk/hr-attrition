"""租户表 ORM（参考 D04 3.1 tenants）."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Tenant(Base, UUIDPKMixin, TimestampMixin):
    """租户表 - SaaS 多租户根表（ADR-002 行级隔离）."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="租户名称")
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, comment="租户编码（唯一）")
    plan: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard", comment="套餐：standard/pro/enterprise"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", comment="状态：active/suspended/closed"
    )
    encryption_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="Fernet 密钥 ID"
    )
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}", comment="租户配置（阈值/通知渠道）"
    )
