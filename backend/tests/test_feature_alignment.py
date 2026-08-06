"""P1-5 特征对齐测试 - 训练/推理侧 salary_percentile 定义一致性 + 特征契约.

覆盖：
  1. salary_percentile：DB 百分位（0-100）→ 模型输入（0-1）换算契约
  2. 边界值裁剪（<0 / >100）
  3. 特征契约断言：列名与列顺序（含元数据存在时的交叉校验）
  4. 推理侧输出与训练侧列顺序一致
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest


class _FakeEmp:
    """Employee ORM 替身，仅含 feature_provider 所需字段."""

    def __init__(self, emp_id="emp-1", salary_percentile=None, dept="R&D"):
        self.id = emp_id
        self.salary_percentile = salary_percentile
        self.position = "developer"
        self.birth_date = date(1990, 5, 1)
        self.hire_date = date(2018, 3, 1)


# ===== 1. salary_percentile 定义一致性 =====

def test_salary_percentile_scale_contract():
    """DB 百分位 100 应换算为模型输入 1.0，50 → 0.5，0 → 0.0（量纲一致）. """
    from app.ml.feature_provider import (
        SALARY_PERCENTILE_SCALE,
        _salary_percentile_value,
    )

    assert SALARY_PERCENTILE_SCALE == 0.01
    emp = _FakeEmp(salary_percentile=100)
    assert _salary_percentile_value(emp) == pytest.approx(1.0)
    emp = _FakeEmp(salary_percentile=50)
    assert _salary_percentile_value(emp) == pytest.approx(0.5)
    emp = _FakeEmp(salary_percentile=0)
    assert _salary_percentile_value(emp) == pytest.approx(0.0)


def test_salary_percentile_boundary_clamped():
    """越界百分位应裁剪到 [0, 1]（防脏数据破坏分布）."""
    from app.ml.feature_provider import _salary_percentile_value

    assert _salary_percentile_value(_FakeEmp(salary_percentile=150)) == pytest.approx(1.0)
    assert _salary_percentile_value(_FakeEmp(salary_percentile=-20)) == pytest.approx(0.0)


def test_salary_percentile_missing_defaults_to_median():
    """缺失分位回退 0.5（与训练侧部门分布缺失回退一致）."""
    from app.ml.feature_provider import _salary_percentile_value

    assert _salary_percentile_value(_FakeEmp(salary_percentile=None)) == pytest.approx(0.5)


def test_structured_feature_salary_percentile_in_unit_range():
    """推理侧输出特征矩阵中 salary_percentile 必须落在 [0, 1]."""
    from app.ml.feature_provider import build_structured_features

    for pct in (None, 0, 33, 100):
        df = build_structured_features(_FakeEmp(salary_percentile=pct))
        val = float(df.iloc[0]["salary_percentile"])
        assert 0.0 <= val <= 1.0, f"salary_percentile={val} 超出 [0,1]"


# ===== 2. 特征契约 =====

def test_assert_feature_contract_passes_without_metadata(monkeypatch):
    """元数据缺失时契约校验应跳过（不阻断推理）. """
    from app.ml import feature_provider

    monkeypatch.setattr(feature_provider, "_FEATURE_METADATA_PATH", __import__("pathlib").Path("nonexistent.pkl"))
    monkeypatch.setattr(feature_provider, "_training_metadata_cache", None)
    feature_provider.assert_feature_contract()  # 不应抛错


def test_assert_feature_contract_mismatch_raises(monkeypatch):
    """训练/推理列定义不一致时必须抛错（防静默漂移）. """
    from app.ml import feature_provider

    bad_metadata = {"structured_feature_columns": ["Age", "WRONG_COL"]}
    monkeypatch.setattr(feature_provider, "_training_metadata_cache", bad_metadata)
    with pytest.raises(RuntimeError, match="特征契约不一致"):
        feature_provider.assert_feature_contract()
    monkeypatch.setattr(feature_provider, "_training_metadata_cache", None)


def test_behavior_contract_mismatch_raises(monkeypatch):
    from app.ml import feature_provider

    bad_metadata = {
        "structured_feature_columns": list(feature_provider.STRUCTURED_FEATURE_COLUMNS),
        "behavior_feature_columns": ["email_count_trend_slope", "WRONG"],
    }
    monkeypatch.setattr(feature_provider, "_training_metadata_cache", bad_metadata)
    with pytest.raises(RuntimeError, match="行为特征列"):
        feature_provider.assert_feature_contract()
    monkeypatch.setattr(feature_provider, "_training_metadata_cache", None)


def test_inference_output_matches_training_column_order():
    """推理侧结构化输出列顺序必须等于训练契约 STRUCTURED_FEATURE_COLUMNS."""
    from app.ml.feature_engineering import STRUCTURED_FEATURE_COLUMNS
    from app.ml.feature_provider import build_features

    structured, behavior = build_features(_FakeEmp())
    assert list(structured.columns) == STRUCTURED_FEATURE_COLUMNS
    # 行为列：3 指标 × 4 统计量
    expected_behav = sorted(
        f"{p}_{s}"
        for p in ("email_count", "meeting_decline_rate", "login_count")
        for s in ("trend_slope", "mean", "std", "recent_change_rate")
    )
    assert sorted(behavior.columns.tolist()) == expected_behav


def test_salary_percentile_training_side_unit_range():
    """训练侧 engineer_structured 输出 salary_percentile 也应落在 [0, 1]（双向契约）."""
    from app.ml.feature_engineering import engineer_structured

    raw = pd.DataFrame(
        {
            "Department": ["Sales", "Sales", "R&D"],
            "MonthlyIncome": [3000, 5000, 4000],
            "YearsSinceLastPromotion": [1, 2, 3],
            "YearsAtCompany": [2, 3, 4],
            "TotalWorkingYears": [5, 6, 7],
            "OverTime": ["Yes", "No", "No"],
            "BusinessTravel": ["Travel_Rarely", "Travel_Frequently", "Non-Travel"],
            "MaritalStatus": ["Single", "Married", "Divorced"],
            "Attrition": ["No", "Yes", "No"],
            "Age": [30, 35, 40],
            "DistanceFromHome": [5, 10, 15],
            "Education": [3, 4, 2],
            "EnvironmentSatisfaction": [4, 3, 2],
            "JobInvolvement": [3, 4, 2],
            "JobLevel": [2, 3, 1],
            "JobSatisfaction": [4, 2, 3],
            "NumCompaniesWorked": [3, 5, 1],
            "PercentSalaryHike": [15, 20, 11],
            "PerformanceRating": [4, 3, 3],
            "RelationshipSatisfaction": [3, 4, 2],
            "StockOptionLevel": [1, 2, 0],
            "TrainingTimesLastYear": [3, 2, 4],
            "WorkLifeBalance": [3, 2, 4],
            "YearsInCurrentRole": [1, 2, 3],
            "YearsWithCurrManager": [1, 2, 3],
        }
    )
    X, _ = engineer_structured(raw)
    vals = X["salary_percentile"].to_numpy()
    assert np.all(vals >= 0.0) and np.all(vals <= 1.0)
