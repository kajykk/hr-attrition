"""管理员 schemas - Kill Switch / 漂移检测 / 公平性监测（D03 4.5 + D10 7.3）."""
from typing import Literal

from pydantic import BaseModel


class KillSwitchStatus(BaseModel):
    """Kill Switch 当前状态."""

    active: bool
    reason: str | None = ""
    activated_at: str | None = ""
    activated_by: str | None = ""

    model_config = {"protected_namespaces": ()}


class KillSwitchAction(BaseModel):
    """Kill Switch 激活请求 body."""

    reason: str


class DriftFeature(BaseModel):
    """单个特征的 PSI 漂移值."""

    feature: str
    psi: float


class DriftResult(BaseModel):
    """漂移检测结果（治理视图契约，与前端 DriftResult 对齐）.

    data_source 标注 current 分布来源：
      db:risk_predictions(window=7d) = 线上真实预测输入；
      csv:fallback:* = 静态降级数据（结果仅供参考）。
    """

    max_psi: float
    critical_features: list[str]
    features: list[DriftFeature]
    computed_at: str
    data_source: str | None = None


class FairnessDimension(BaseModel):
    """单个维度的公平性偏差（disparity 为百分比，如 3.2 表示 3.2%）."""

    name: Literal["gender", "age", "ethnicity", "disability"]
    label: str
    disparity: float


class FairnessResult(BaseModel):
    """公平性监测结果（治理视图契约，与前端 FairnessResult 对齐）.

    data_source 标注审计数据来源（当前为训练期静态 CSV，非线上预测流）。
    """

    dimensions: list[FairnessDimension]
    computed_at: str
    data_source: str | None = None
