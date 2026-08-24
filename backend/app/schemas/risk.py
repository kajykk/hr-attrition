"""风险预测 schemas（参考 D05 3.3）."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    employee_id: UUID
    force_refresh: bool = Field(default=False, description="是否强制刷新缓存")


class RiskPredictionOut(BaseModel):
    """预测结果（D05 3.3 POST /risk/predict）."""

    prediction_id: UUID
    employee_id: UUID
    risk_score: int = Field(ge=0, le=100)
    risk_level: str
    modality_scores: dict = Field(
        default_factory=dict,
        description="各模态分数：{structured, text, behavior}",
    )
    model_version: str
    predicted_at: datetime
    cached: bool = False
    behavior_data_source: str | None = Field(
        default=None,
        description="行为时序数据来源：demo=当前版本演示数据"
        "（由 employee.id 播种生成）；real=真实行为日志",
    )

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class ShapFactor(BaseModel):
    feature: str
    display_name: str
    value: float | None = None
    contribution: float
    direction: str = Field(description="positive/negative")
    description: str | None = None


class ShapExplanationOut(BaseModel):
    prediction_id: UUID
    factors: list[ShapFactor]
    base_value: float
    output_value: float
    computed_at: datetime


class GlobalExplanationOut(BaseModel):
    """全局特征重要性（D05 3.10 GET /risk/global-explanation，近 30 天聚合）."""

    model_version: str
    window_days: int = 30
    top_features: list[ShapFactor]
    computed_at: datetime

    model_config = {"protected_namespaces": ()}
