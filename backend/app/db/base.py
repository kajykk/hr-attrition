"""SQLAlchemy DeclarativeBase + 通用 mixin（参考 D04 1.1 审计字段统一）."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类."""



class TimestampMixin:
    """统一审计字段：created_at / updated_at（参考 D04 1.1）."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPKMixin:
    """UUID 主键 mixin（UUID v4，生产建议 v7）."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class SoftDeleteMixin:
    """软删除 mixin（deleted_at 字段，业务表通用）."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


class TenantMixin:
    """多租户行级隔离 mixin（ADR-002）：每张业务表必须含 tenant_id."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        index=True,
        nullable=False,
        comment="租户 ID（行级隔离 ADR-002）",
    )
