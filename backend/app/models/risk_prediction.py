"""风险预测表 ORM（参考 D04 3.3 risk_predictions）."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPKMixin

# 风险等级枚举（D04 4.1）
RISK_LEVEL_LOW = "low"                     # 0-19 绿色
RISK_LEVEL_MEDIUM_LOW = "medium_low"       # 20-39 蓝色
RISK_LEVEL_MEDIUM = "medium"               # 40-59 黄色
RISK_LEVEL_MEDIUM_HIGH = "medium_high"     # 60-79 橙色
RISK_LEVEL_HIGH = "high"                   # 80-100 红色


class RiskPrediction(Base, UUIDPKMixin, TenantMixin):
    """风险预测表 - 多模态融合引擎输出.

    分区策略：按 predicted_at 月度分区，保留 2 年（D04 5.2）。
    分区表主键须含分区键，故主键为 (id, predicted_at)。
    """

    __tablename__ = "risk_predictions"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id"), nullable=False, index=True
    )
    model_version: Mapped[str] = mapped_column(String(50), nullable=False, comment="模型版本")
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, comment="风险分 0-100")
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, comment="风险等级")
    modality_scores: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}", comment="各模态分数（structured/text/behavior）"
    )
    feature_values: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}", comment="输入特征值"
    )
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, comment="预测时间（分区键）"
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True, comment="批次 ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
