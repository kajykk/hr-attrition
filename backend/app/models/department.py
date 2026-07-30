"""部门表 ORM（参考 D04 3.2 departments）."""
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPKMixin


class Department(Base, UUIDPKMixin, TenantMixin, TimestampMixin):
    """部门表 - 树形组织结构（parent_id 自引用 + path 层级路径）."""

    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="部门名称")
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id"),
        nullable=True,
        index=True,
        comment="上级部门 ID",
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="部门负责人 ID"
    )
    path: Mapped[str] = mapped_column(String(500), nullable=False, default="", comment="层级路径 root.eng.backend")
    headcount: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="编制人数")
