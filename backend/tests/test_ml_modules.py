"""ML 模块单元测试 - 数据生成 / 特征工程 / 融合引擎 / SHAP / 公平性.

覆盖：
  - data_generation.generate_structured_data / generate_behavior_data / generate_all
  - feature_engineering.engineer_structured / engineer_behavior / load_split
  - fusion_engine.FusionEngine.predict / predict_batch / score_to_level / evaluate
  - shap_explainer.ShapExplainer.explain（含方向与排序）
  - fairness_test.run_fairness_test / compute_fairness（4 维度偏差）

模型文件不存在时用 skipif 跳过对应测试。
所有文件写入用 tmp_path 避免污染真实数据。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from app.ml.data_generation import (
    AUDIT_COLUMNS as DG_AUDIT_COLUMNS,
)
from app.ml.data_generation import (
    N_MONTHS,
    compute_attrition_probability,
    generate_behavior_data,
    generate_structured_data,
)
from app.ml.feature_engineering import (
    STRUCTURED_FEATURE_COLUMNS,
    engineer_behavior,
    engineer_structured,
    load_split,
)
from app.ml.fusion_engine import (
    FUSION_METRICS_PATH,
    TEST_PREDICTIONS_PATH,
    FusionEngine,
    score_to_level,
)

# ===== 模型文件存在性检测（与 test_risk_service.py 保持一致） =====
_MODELS_DIR = Path(__file__).resolve().parent.parent / "app" / "ml" / "models"
_STRUCT_MODEL_PATH = _MODELS_DIR / "structured_lgbm.pkl"
_BEHAVIOR_MODEL_PATH = _MODELS_DIR / "behavior_if.pkl"
_MODELS_AVAILABLE = _STRUCT_MODEL_PATH.exists() and _BEHAVIOR_MODEL_PATH.exists()
_skip_if_no_models = pytest.mark.skipif(
    not _MODELS_AVAILABLE,
    reason="模型文件不存在（structured_lgbm.pkl / behavior_if.pkl），跳过模型依赖测试",
)


# ============================================================
# 1. data_generation 测试
# ============================================================


def test_generate_structured_data_returns_required_columns():
    """generate_structured_data 应返回含模型特征 + 审计字段 + Attrition 的 DataFrame."""
    # 用 numpy 路径（避免依赖 SDV），生成少量样本提速
    df = generate_structured_data(n=50, seed=42, use_sdv=False)

    # 必含模型特征列
    for col in ["Age", "MonthlyIncome", "OverTime", "Department", "MaritalStatus"]:
        assert col in df.columns, f"缺少必要列：{col}"

    # 必含审计字段（gender/ethnicity/disability/birth_date）
    for col in DG_AUDIT_COLUMNS:
        assert col in df.columns, f"缺少审计字段：{col}"

    # 必含标签
    assert "Attrition" in df.columns
    # Attrition 取值只能是 Yes / No
    assert set(df["Attrition"].unique()).issubset({"Yes", "No"})

    # 行数符合预期
    assert len(df) == 50


def test_generate_structured_data_respects_size():
    """generate_structured_data 应生成指定行数."""
    df = generate_structured_data(n=30, seed=7, use_sdv=False)
    assert len(df) == 30


def test_generate_structured_data_deterministic_with_same_seed():
    """同种子应生成相同数据（确定性）."""
    df1 = generate_structured_data(n=20, seed=42, use_sdv=False)
    df2 = generate_structured_data(n=20, seed=42, use_sdv=False)
    pd.testing.assert_frame_equal(df1, df2)


def test_generate_structured_data_constraints_satisfied():
    """生成数据应满足年限嵌套约束（YearsAtCompany <= TotalWorkingYears 等）."""
    df = generate_structured_data(n=100, seed=42, use_sdv=False)
    # YearsAtCompany <= TotalWorkingYears
    assert (df["YearsAtCompany"] <= df["TotalWorkingYears"]).all()
    # YearsInCurrentRole <= YearsAtCompany
    assert (df["YearsInCurrentRole"] <= df["YearsAtCompany"]).all()
    # YearsWithCurrManager <= YearsAtCompany
    assert (df["YearsWithCurrManager"] <= df["YearsAtCompany"]).all()
    # YearsSinceLastPromotion <= YearsAtCompany
    assert (df["YearsSinceLastPromotion"] <= df["YearsAtCompany"]).all()
    # MonthlyIncome 在合法范围
    assert (df["MonthlyIncome"] >= 1000).all()
    assert (df["MonthlyIncome"] <= 20000).all()
    # Age 在合法范围
    assert (df["Age"] >= 18).all()
    assert (df["Age"] <= 60).all()


def test_compute_attrition_probability_returns_valid_probabilities():
    """compute_attrition_probability 应返回 [0, 1] 范围的概率."""
    df = generate_structured_data(n=100, seed=42, use_sdv=False)
    p, bonus = compute_attrition_probability(df)

    # 概率在 [0, 1]
    assert (p >= 0.0).all()
    assert (p <= 1.0).all()
    # bonus 非负
    assert (bonus >= 0).all()
    # 加班员工 bonus 更高
    ot_bonus = bonus[df["OverTime"].eq("Yes").to_numpy()]
    no_ot_bonus = bonus[df["OverTime"].eq("No").to_numpy()]
    if len(ot_bonus) > 0 and len(no_ot_bonus) > 0:
        assert ot_bonus.mean() > no_ot_bonus.mean()


def test_generate_behavior_data_returns_12_month_series():
    """generate_behavior_data 应返回 12 个月时序数据."""
    structured = generate_structured_data(n=30, seed=42, use_sdv=False)
    behavior = generate_behavior_data(structured, seed=42)

    # 行数一致
    assert len(behavior) == len(structured)
    # employee_id 列存在
    assert "employee_id" in behavior.columns
    # 12 个月 × 3 指标 = 36 列 + employee_id = 37 列
    assert len(behavior.columns) == 36 + 1

    # 每个指标有 12 个月列
    for prefix in ["email_count", "meeting_decline_rate", "login_count"]:
        month_cols = [c for c in behavior.columns if c.startswith(f"{prefix}_month_")]
        assert len(month_cols) == N_MONTHS

    # meeting_decline_rate 在 [0, 1]
    decline_cols = [c for c in behavior.columns if c.startswith("meeting_decline_rate_month_")]
    for col in decline_cols:
        assert (behavior[col] >= 0.0).all()
        assert (behavior[col] <= 1.0).all()


def test_generate_behavior_data_attrition_employees_show_decline_trend():
    """离职员工后期 email_count 应低于前期（异常下降趋势）."""
    structured = generate_structured_data(n=200, seed=42, use_sdv=False)
    behavior = generate_behavior_data(structured, seed=42)

    # 标注离职员工
    is_attrition = structured["Attrition"].eq("Yes").to_numpy()
    if is_attrition.sum() == 0:
        pytest.skip("无离职样本，无法验证趋势")

    # 离职员工：前 3 月均值 vs 后 3 月均值
    early = behavior.loc[is_attrition, "email_count_month_1":"email_count_month_3"].mean(axis=1)
    late = behavior.loc[is_attrition, "email_count_month_10":"email_count_month_12"].mean(axis=1)
    # 多数离职员工后期 email 下降
    decline_rate = (late < early).mean()
    assert decline_rate > 0.6, f"离职员工后期 email 下降比例 {decline_rate:.2%} 应 > 60%"


def test_generate_all_writes_files(tmp_path, monkeypatch):
    """generate_all 应将文件写入 data/raw/ 目录（用 tmp_path 隔离）."""
    from app.ml import data_generation

    # patch RAW_DIR 指向临时目录，避免污染真实数据
    monkeypatch.setattr(data_generation, "RAW_DIR", tmp_path)

    structured, behavior = data_generation.generate_all(n=20, seed=42)

    # 文件应存在
    assert (tmp_path / "structured_train.csv").exists()
    assert (tmp_path / "behavior_train.csv").exists()

    # 返回值类型与行数
    assert len(structured) == 20
    assert len(behavior) == 20
    assert "Attrition" in structured.columns
    assert "employee_id" in behavior.columns


def test_generate_structured_data_attrition_rate_in_reasonable_range():
    """生成数据的离职率应在合理区间（5%-40%）."""
    df = generate_structured_data(n=500, seed=42, use_sdv=False)
    rate = df["Attrition"].eq("Yes").mean()
    assert 0.05 <= rate <= 0.40, f"离职率 {rate:.2%} 不在合理区间"


# ============================================================
# 2. feature_engineering 测试
# ============================================================


def test_engineer_structured_returns_correct_columns():
    """engineer_structured 输出列应等于 STRUCTURED_FEATURE_COLUMNS."""
    df = generate_structured_data(n=30, seed=42, use_sdv=False)
    X, dept_income = engineer_structured(df)

    assert list(X.columns) == STRUCTURED_FEATURE_COLUMNS
    assert len(X) == len(df)
    # dept_income_sorted 应含 3 个部门
    assert set(dept_income.keys()) == {"Sales", "R&D", "HR"}


def test_engineer_structured_no_pii_fields():
    """engineer_structured 输出绝不能含 gender/ethnicity/disability/birth_date."""
    df = generate_structured_data(n=20, seed=42, use_sdv=False)
    X, _ = engineer_structured(df)

    forbidden = {"gender", "ethnicity", "disability", "birth_date", "age_derived"}
    assert not (set(X.columns) & forbidden), f"特征中含禁用字段：{set(X.columns) & forbidden}"


def test_engineer_structured_salary_percentile_in_unit_interval():
    """salary_percentile 应在 [0, 1] 区间."""
    df = generate_structured_data(n=50, seed=42, use_sdv=False)
    X, _ = engineer_structured(df)

    assert (X["salary_percentile"] >= 0.0).all()
    assert (X["salary_percentile"] <= 1.0).all()


def test_engineer_structured_one_hot_exclusivity():
    """部门/婚姻 one-hot 编码应互斥（每行仅一个 1）."""
    df = generate_structured_data(n=50, seed=42, use_sdv=False)
    X, _ = engineer_structured(df)

    # 部门 one-hot：每行恰好一个 1
    dept_cols = ["dept_Sales", "dept_RD", "dept_HR"]
    dept_sum = X[dept_cols].sum(axis=1)
    assert (dept_sum == 1).all(), "部门 one-hot 不互斥"

    # 婚姻 one-hot：每行恰好一个 1
    marital_cols = ["marital_Single", "marital_Married", "marital_Divorced"]
    marital_sum = X[marital_cols].sum(axis=1)
    assert (marital_sum == 1).all(), "婚姻 one-hot 不互斥"


def test_engineer_structured_reuses_dept_income():
    """传入 dept_income_sorted 时应复用（推理时一致性）."""
    df_train = generate_structured_data(n=50, seed=42, use_sdv=False)
    _, dept_income = engineer_structured(df_train)

    # 用同一 dept_income 处理新数据
    df_new = generate_structured_data(n=10, seed=99, use_sdv=False)
    X_new, dept_income2 = engineer_structured(df_new, dept_income_sorted=dept_income)

    # dept_income 应原样返回（复用）
    assert dept_income2 is dept_income
    assert list(X_new.columns) == STRUCTURED_FEATURE_COLUMNS


def test_engineer_behavior_returns_12_columns():
    """engineer_behavior 应返回 12 列（3 指标 × 4 统计量）."""
    structured = generate_structured_data(n=20, seed=42, use_sdv=False)
    behavior = generate_behavior_data(structured, seed=42)
    feats = engineer_behavior(behavior)

    assert len(feats.columns) == 12
    assert len(feats) == len(behavior)

    # 验证列名后缀
    stats = {"trend_slope", "mean", "std", "recent_change_rate"}
    for col in feats.columns:
        assert any(col.endswith(f"_{s}") for s in stats), f"列 {col} 不以统计量结尾"


def test_engineer_behavior_statistical_values_finite():
    """engineer_behavior 输出值应全有限（无 inf / NaN）."""
    structured = generate_structured_data(n=20, seed=42, use_sdv=False)
    behavior = generate_behavior_data(structured, seed=42)
    feats = engineer_behavior(behavior)

    assert not feats.isna().any().any(), "行为特征含 NaN"
    assert np.isfinite(feats.to_numpy()).all(), "行为特征含 inf"


def test_load_split_returns_all_dataframes():
    """load_split 应返回 6 个特征/标签 DataFrame + audit_test + metadata."""
    data = load_split()

    expected_keys = [
        "X_struct_train", "X_struct_val", "X_struct_test",
        "X_behav_train", "X_behav_val", "X_behav_test",
        "y_train", "y_val", "y_test", "audit_test", "metadata",
    ]
    for key in expected_keys:
        assert key in data, f"load_split 缺少键：{key}"

    # 结构化特征列顺序一致
    assert list(data["X_struct_train"].columns) == STRUCTURED_FEATURE_COLUMNS

    # y 是 int Series
    for k in ["y_train", "y_val", "y_test"]:
        assert data[k].dtype.kind in {"i", "u"}, f"{k} 应为整数类型，实际 {data[k].dtype}"

    # metadata 含必要字段
    meta = data["metadata"]
    assert "structured_feature_columns" in meta
    assert "dept_income_sorted" in meta
    assert meta["structured_feature_columns"] == STRUCTURED_FEATURE_COLUMNS


def test_load_split_audit_test_contains_pii_audit_fields():
    """audit_test 应含 gender/ethnicity/disability/age_derived 审计字段."""
    data = load_split()
    audit = data["audit_test"]
    for col in ["gender", "age_derived", "ethnicity", "disability"]:
        assert col in audit.columns, f"audit_test 缺少审计字段：{col}"


# ============================================================
# 3. fusion_engine 测试
# ============================================================


def test_score_to_level_all_thresholds():
    """score_to_level 应覆盖所有阈值边界."""
    assert score_to_level(0) == "low"
    assert score_to_level(19) == "low"
    assert score_to_level(20) == "medium_low"
    assert score_to_level(39) == "medium_low"
    assert score_to_level(40) == "medium"
    assert score_to_level(59) == "medium"
    assert score_to_level(60) == "medium_high"
    assert score_to_level(79) == "medium_high"
    assert score_to_level(80) == "high"
    assert score_to_level(100) == "high"


def test_score_to_level_extreme_values():
    """score_to_level 应处理边界极值."""
    assert score_to_level(0) == "low"
    assert score_to_level(100) == "high"


@_skip_if_no_models
def test_fusion_engine_predict_returns_valid_score():
    """FusionEngine.predict 应返回 0-100 的 risk_score 与合法 level."""
    from app.ml.feature_provider import build_features

    class _FakeEmp:
        id = uuid4()
        birth_date = date(1990, 5, 15)
        hire_date = date(2018, 3, 1)
        salary_percentile = None
        position = "Senior Sales Engineer"
        level = "P6"

    structured, behavior = build_features(_FakeEmp())
    engine = FusionEngine()
    result = engine.predict(structured, behavior)

    assert 0 <= result["risk_score"] <= 100
    assert result["risk_level"] in {"low", "medium_low", "medium", "medium_high", "high"}
    assert "structured" in result["modality_scores"]
    assert "behavior" in result["modality_scores"]
    # 模态分数在 [0, 1]
    assert 0.0 <= result["modality_scores"]["structured"] <= 1.0
    assert 0.0 <= result["modality_scores"]["behavior"] <= 1.0


@_skip_if_no_models
def test_fusion_engine_predict_batch_returns_multiple_rows():
    """FusionEngine.predict_batch 应返回多行预测结果."""
    from app.ml.feature_provider import build_features

    class _FakeEmp:
        id = uuid4()
        birth_date = date(1990, 5, 15)
        hire_date = date(2018, 3, 1)
        salary_percentile = None
        position = "Senior Sales Engineer"
        level = "P6"

    # 构造 5 行特征（重复单行）
    s_one, b_one = build_features(_FakeEmp())
    structured = pd.concat([s_one] * 5, ignore_index=True)
    behavior = pd.concat([b_one] * 5, ignore_index=True)

    engine = FusionEngine()
    results = engine.predict_batch(structured, behavior)

    assert len(results) == 5
    for r in results:
        assert 0 <= r["risk_score"] <= 100
        assert r["risk_level"] in {"low", "medium_low", "medium", "medium_high", "high"}
        assert "score_final" in r
        assert "score_structured" in r
        assert "score_behavior" in r


@_skip_if_no_models
def test_fusion_engine_score_to_level_consistent_with_predictions():
    """FusionEngine 返回的 risk_level 应与 score_to_level(risk_score) 一致."""
    from app.ml.feature_provider import build_features

    class _FakeEmp:
        id = uuid4()
        birth_date = date(1985, 1, 1)
        hire_date = date(2015, 6, 1)
        salary_percentile = None
        position = "Sales"
        level = "P5"

    structured, behavior = build_features(_FakeEmp())
    engine = FusionEngine()
    result = engine.predict(structured, behavior)

    assert result["risk_level"] == score_to_level(result["risk_score"])


@_skip_if_no_models
def test_fusion_engine_evaluate_returns_metrics_dict():
    """FusionEngine.evaluate() 应返回含 AUC / recall 等指标的 dict."""
    # evaluate 会读取 load_split() 数据并覆盖 test_predictions.csv
    # 注意：此测试会重写 models/test_predictions.csv 与 fusion_metrics.json
    from app.ml.fusion_engine import evaluate

    metrics = evaluate()

    # 必要字段
    assert "auc_test" in metrics
    assert "recall_at_top20" in metrics
    assert "precision_at_top20" in metrics
    assert "top20_size" in metrics
    assert "total_attrition" in metrics
    assert "classification_report" in metrics
    assert "weights" in metrics
    assert "passed" in metrics

    # 类型校验
    assert isinstance(metrics["auc_test"], float)
    assert 0.0 <= metrics["auc_test"] <= 1.0
    assert isinstance(metrics["weights"], dict)
    assert metrics["weights"]["structured"] == 0.7
    assert metrics["weights"]["behavior"] == 0.3

    # 产物文件应存在
    assert FUSION_METRICS_PATH.exists()
    assert TEST_PREDICTIONS_PATH.exists()


# ============================================================
# 4. shap_explainer 测试
# ============================================================


@_skip_if_no_models
def test_shap_explainer_returns_top3_factors():
    """ShapExplainer.explain 应返回 Top3 特征贡献."""
    from app.ml.feature_provider import build_structured_features
    from app.ml.shap_explainer import ShapExplainer

    class _FakeEmp:
        id = uuid4()
        birth_date = date(1990, 5, 15)
        hire_date = date(2018, 3, 1)
        salary_percentile = None
        position = "Sales"
        level = "P5"

    structured = build_structured_features(_FakeEmp())
    explainer = ShapExplainer()
    factors = explainer.explain(structured, top_k=3)

    assert len(factors) == 3
    for f in factors:
        assert "feature" in f
        assert "contribution" in f
        assert "direction" in f
        assert f["direction"] in {"positive", "negative"}
        # feature 必须在结构化特征列中
        assert f["feature"] in STRUCTURED_FEATURE_COLUMNS


@_skip_if_no_models
def test_shap_explainer_factors_ordered_by_abs_contribution():
    """SHAP factors 应按 |contribution| 降序排列."""
    from app.ml.feature_provider import build_structured_features
    from app.ml.shap_explainer import ShapExplainer

    class _FakeEmp:
        id = uuid4()
        birth_date = date(1990, 5, 15)
        hire_date = date(2018, 3, 1)
        salary_percentile = None
        position = "Sales"
        level = "P5"

    structured = build_structured_features(_FakeEmp())
    explainer = ShapExplainer()
    factors = explainer.explain(structured, top_k=5)

    abs_contribs = [abs(f["contribution"]) for f in factors]
    assert abs_contribs == sorted(abs_contribs, reverse=True)


@_skip_if_no_models
def test_shap_explainer_direction_matches_contribution_sign():
    """SHAP direction 应与 contribution 符号一致：>0 → positive, <0 → negative."""
    from app.ml.feature_provider import build_structured_features
    from app.ml.shap_explainer import ShapExplainer

    class _FakeEmp:
        id = uuid4()
        birth_date = date(1990, 5, 15)
        hire_date = date(2018, 3, 1)
        salary_percentile = None
        position = "Sales"
        level = "P5"

    structured = build_structured_features(_FakeEmp())
    explainer = ShapExplainer()
    factors = explainer.explain(structured, top_k=10)

    for f in factors:
        if f["contribution"] > 0:
            assert f["direction"] == "positive"
        elif f["contribution"] < 0:
            assert f["direction"] == "negative"
        # contribution == 0 时 direction 应为 negative（实现中 v > 0 ? positive : negative）


@_skip_if_no_models
def test_shap_explainer_topk_respected():
    """top_k 参数应被尊重."""
    from app.ml.feature_provider import build_structured_features
    from app.ml.shap_explainer import ShapExplainer

    class _FakeEmp:
        id = uuid4()
        birth_date = date(1990, 5, 15)
        hire_date = date(2018, 3, 1)
        salary_percentile = None
        position = "Sales"
        level = "P5"

    structured = build_structured_features(_FakeEmp())
    explainer = ShapExplainer()
    factors_top1 = explainer.explain(structured, top_k=1)
    factors_top5 = explainer.explain(structured, top_k=5)

    assert len(factors_top1) == 1
    assert len(factors_top5) == 5


# ============================================================
# 5. fairness_test 测试
# ============================================================


@_skip_if_no_models
def test_run_fairness_test_returns_passed_true():
    """run_fairness_test 应返回 overall_passed=True（用训练好的模型预测）.

    模型已应用公平性硬约束（特征中不含 gender/ethnicity/disability），
    预测分布应通过 5% 偏差阈值。
    """
    from app.ml.fairness_test import FAIRNESS_REPORT_PATH, run_fairness_test

    result = run_fairness_test()

    # 必要字段
    assert "default_threshold" in result
    assert "final_threshold" in result
    assert "mitigation_applied" in result
    assert "dimensions" in result
    assert "max_parity_difference" in result
    assert "overall_passed" in result

    # 4 维度
    expected_dims = {"gender", "age", "ethnicity", "disability"}
    assert set(result["dimensions"].keys()) == expected_dims

    # 公平性硬约束已保证偏差 < 5%（即便 mitigation 未触发也应通过）
    assert result["overall_passed"] is True, (
        f"公平性测试未通过 | max_parity={result['max_parity_difference']:.4f}"
    )

    # 报告文件应存在
    assert FAIRNESS_REPORT_PATH.exists()


def test_fairness_group_parity_computes_difference():
    """_group_parity 应正确计算组间偏差."""
    from app.ml.fairness_test import _group_parity

    # 构造两组：M 高风险率 0.8，F 高风险率 0.2 → diff=0.6
    df = pd.DataFrame({
        "gender": ["M"] * 10 + ["F"] * 10,
        "risk_score": [80] * 8 + [10] * 2 + [80] * 2 + [10] * 8,
    })

    result = _group_parity(df, "gender", threshold=60)

    assert "groups" in result
    assert "parity_difference" in result
    assert "passed" in result
    # M 高风险率 0.8, F 高风险率 0.2 → diff 0.6
    assert abs(result["parity_difference"] - 0.6) < 1e-6
    # 0.6 >= 0.05 → 未通过
    assert result["passed"] is False
    assert set(result["groups"].keys()) == {"M", "F"}


def test_fairness_group_parity_passes_when_no_difference():
    """两组高风险率相同时 parity_difference=0，应通过."""
    from app.ml.fairness_test import _group_parity

    df = pd.DataFrame({
        "gender": ["M"] * 10 + ["F"] * 10,
        "risk_score": [80] * 5 + [10] * 5 + [80] * 5 + [10] * 5,
    })
    result = _group_parity(df, "gender", threshold=60)
    assert result["parity_difference"] == 0.0
    assert result["passed"] is True


def test_fairness_build_dimensions_returns_four_dimensions():
    """_build_dimensions 应返回 4 个维度的分组列."""
    from app.ml.fairness_test import _build_dimensions

    df = pd.DataFrame({
        "gender": ["M", "F", "M", "F"],
        "age_derived": [25, 40, 30, 50],
        "ethnicity": [0, 1, 0, 1],
        "disability": [0, 1, 0, 1],
        "risk_score": [70, 50, 80, 30],
    })

    dims, work = _build_dimensions(df)

    assert set(dims.keys()) == {"gender", "age", "ethnicity", "disability"}
    # work 应含构造的分组列
    assert "age_group" in work.columns
    assert "ethnicity_label" in work.columns
    assert "disability_label" in work.columns
    # age_group 取值
    assert set(work["age_group"].unique()).issubset({"<35", ">=35"})
    # ethnicity_label 取值
    assert set(work["ethnicity_label"].unique()).issubset({"汉族", "少数民族"})


def test_fairness_evaluate_at_threshold_returns_max_parity():
    """_evaluate_at_threshold 应返回各维度报告与最大偏差."""
    from app.ml.fairness_test import _evaluate_at_threshold

    df = pd.DataFrame({
        "gender": ["M", "F"] * 20,
        "age_derived": [25, 40] * 20,
        "ethnicity": [0, 1] * 20,
        "disability": [0, 1] * 20,
        "risk_score": [70, 30] * 20,  # M 全高风险，F 全低风险
    })

    report, max_parity = _evaluate_at_threshold(df, threshold=60)

    assert set(report.keys()) == {"gender", "age", "ethnicity", "disability"}
    # gender 维度应有大偏差
    assert report["gender"]["parity_difference"] > 0.5
    # max_parity 至少为 gender 维度的偏差
    assert max_parity >= report["gender"]["parity_difference"]
