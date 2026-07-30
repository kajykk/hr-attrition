"""审计日志表 ORM - 哈希链防篡改（参考 D04 3.6 audit_logs）.

哈希链规则（D03 6.3）：
  - 每条记录含 prev_hash（上一条 current_hash）+ current_hash
  - current_hash = sha256(prev_hash + payload + created_at)
  - 任何篡改会导致链断裂，可被 audit_service 校验
  - 日志保留 5 年，分区：按 created_at 月度分区
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPKMixin


class AuditLog(Base, UUIDPKMixin, TenantMixin):
    """审计日志表 - 所有写操作 + PII 访问单独记录.

    分区表主键须含分区键 created_at。
    """

    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True, comment="操作人 ID")
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, comment="动作")
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False, comment="资源类型")
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    before_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="变更前")
    after_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="变更后")

    # 哈希链字段（防篡改）
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="上一条哈希")
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="当前条哈希")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, comment="创建时间（分区键）"
    )
