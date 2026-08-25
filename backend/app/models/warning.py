"""预警表 + 预警事件表 ORM（参考 D04 3.4）.

预警状态机（D04 4.3 + V1.1 修订）：
  new       → confirmed, closed
  confirmed → review（P0 必经复核）, fixing（P1/P2 可直转）, appealing, closed
  fixing    → review, closed
  review    → closed, fixing
  appealing → confirmed, closed
  closed    → 终态

P0 强制路径：confirmed → review → fixing（FR-LOOP-004，HR 经理复核后方可干预）
P1/P2 路径：confirmed → fixing（直转，无需复核）
转换逻辑在 WarningService 中按 warning.level 条件分支实现。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPKMixin

# ===== 预警等级（D04 4.2） =====
LEVEL_P0 = "P0"  # risk_score >= 80，WebSocket + 邮件 + 短信，24h 升级
LEVEL_P1 = "P1"  # 60 <= risk_score < 80，WebSocket + 邮件，48h 升级
LEVEL_P2 = "P2"  # risk_score 上升 >= 20，WebSocket，72h 升级

# 等级严重度排序（低 → 高，供升级/合并判定）
LEVEL_ORDER: list[str] = [LEVEL_P2, LEVEL_P1, LEVEL_P0]

# ===== 预警状态（D04 4.3） =====
STATUS_NEW = "new"
STATUS_CONFIRMED = "confirmed"
STATUS_REVIEW = "review"
STATUS_FIXING = "fixing"
STATUS_APPEALING = "appealing"
STATUS_CLOSED = "closed"

# 未关闭（active/open）状态全集：防重复建警与自动升级的作用域
ACTIVE_STATUSES: tuple[str, ...] = (
    STATUS_NEW,
    STATUS_CONFIRMED,
    STATUS_REVIEW,
    STATUS_FIXING,
    STATUS_APPEALING,
)

# 单条预警允许发起申诉的最大次数（超出后 appealing 入口返回 409）
MAX_APPEAL_COUNT = 3

# 部分唯一索引（迁移 0006）：同一租户同一员工同一等级仅允许一条未关闭预警，
# 应用层 create_warning 先查后插 + 冲突 retry，DB 层该索性兜底并发窗口
UQ_ACTIVE_WARNINGS_INDEX = "uq_warnings_active_tenant_emp_level"

# 合法状态转换映射（与 WarningService.TRANSITIONS 保持一致）
ALLOWED_TRANSITIONS: dict[str, list[str]] = {
    STATUS_NEW: [STATUS_CONFIRMED, STATUS_CLOSED],
    STATUS_CONFIRMED: [STATUS_REVIEW, STATUS_FIXING, STATUS_APPEALING, STATUS_CLOSED],
    STATUS_FIXING: [STATUS_REVIEW, STATUS_CLOSED],
    STATUS_REVIEW: [STATUS_CLOSED, STATUS_FIXING],
    STATUS_APPEALING: [STATUS_CONFIRMED, STATUS_CLOSED],
    STATUS_CLOSED: [],
}


class WarningRecord(Base, UUIDPKMixin, TenantMixin):
    """预警主表."""

    __tablename__ = "warnings"
    __table_args__ = (
        Index(
            UQ_ACTIVE_WARNINGS_INDEX,
            "tenant_id",
            "employee_id",
            "level",
            unique=True,
            postgresql_where=text("status IN ('new', 'confirmed', 'review', 'fixing', 'appealing')"),
        ),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id"), nullable=False, index=True
    )
    prediction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="关联预测 ID"
    )
    level: Mapped[str] = mapped_column(String(20), nullable=False, index=True, comment="等级：P0/P1/P2")
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, comment="触发时风险分")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_NEW, index=True, comment="状态机当前态"
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True, comment="分配 HRBP ID"
    )
    escalated_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="升级到 HR 经理 ID"
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="预警消息")
    appeal_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="已发起申诉次数（>= MAX_APPEAL_COUNT 后 appealing 入口拒绝）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WarningEvent(Base, UUIDPKMixin, TenantMixin):
    """预警事件表 - 记录每次状态转换（审计追溯）."""

    __tablename__ = "warning_events"

    warning_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warnings.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="动作：created/confirmed/escalated/fixing/review/closed/commented",
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="原状态")
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="新状态")
    operator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, comment="操作人 ID")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
