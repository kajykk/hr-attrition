"""T-302 特征工程 - 结构化特征 + 行为时序特征.

职责：
  1. 结构化特征：编码分类变量、构造派生特征（salary_percentile / promotion_gap_months /
     tenure_ratio），输出模型特征矩阵（绝对不含 gender/ethnicity/disability/birth_date）。
  2. 行为特征：对 12 个月时序提取趋势斜率、均值、标准差、近 3 月 vs 前 9 月变化率。
  3. 数据划分：60% train / 20% val / 20% test（分层采样），保存到 data/processed/。
  4. 输出 audit_df（含 gender/age/ethnicity/disability），仅用于公平性审计。

公平性硬约束：gender/ethnicity/disability/birth_date 永不出现在模型特征中。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from app.ml.data_generation import RAW_DIR

PROCESSED_DIR = RAW_DIR.parent / "processed"
MODELS_DIR = Path(__file__).resolve().parent / "models"

SPLIT_SEED = 42
TRAIN_RATIO = 0.6
VAL_RATIO = 0.2
TEST_RATIO = 0.2

# BusinessTravel 序数映射（频次越高越偏向出差，潜在离职影响递增）
BUSINESS_TRAVEL_ORD = {"Non-Travel": 0, "Travel_Rarely": 1, "Travel_Frequently": 2}
DEPARTMENTS = ["Sales", "R&D", "HR"]
MARITAL_STATUSES = ["Single", "Married", "Divorced"]

# 行为时序前缀
BEHAVIOR_SERIES = ["email_count", "meeting_decline_rate", "login_count"]
N_MONTHS = 12

# 最终结构化特征列顺序（模型输入契约）
STRUCTURED_FEATURE_COLUMNS = [
    # 原始数值特征
    "Age", "DistanceFromHome", "Education", "EnvironmentSatisfaction",
    "JobInvolvement", "JobLevel", "JobSatisfaction", "MonthlyIncome",
    "NumCompaniesWorked", "PercentSalaryHike", "PerformanceRating",
    "RelationshipSatisfaction", "StockOptionLevel", "TotalWorkingYears",
    "TrainingTimesLastYear", "WorkLifeBalance", "YearsAtCompany",
    "YearsInCurrentRole", "YearsSinceLastPromotion", "YearsWithCurrManager",
    # 编码分类
    "overtime_flag", "business_travel_ord",
    "dept_Sales", "dept_RD", "dept_HR",
    "marital_Single", "marital_Married", "marital_Divorced",
    # 派生特征
    "salary_percentile", "promotion_gap_months", "tenure_ratio",
]

# 审计字段（仅公平性测试用，不入模型）
AUDIT_COLUMNS = ["gender", "age_derived", "ethnicity", "disability"]


def _safe_col_name(name: str) -> str:
    return name.replace("&", "").replace(" ", "_")


def engineer_structured(
    df: pd.DataFrame,
    dept_income_sorted: dict[str, np.ndarray] | None = None,
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """构造结构化模型特征.

    Args:
        df: 含原始结构化字段的 DataFrame（含审计字段与 Attrition，将被剔除）。
        dept_income_sorted: 各部门训练集 MonthlyIncome 升序数组（用于薪资分位）。
            None 时用当前 df 自身分布计算（训练阶段）。

    Returns:
        X: 特征矩阵（列顺序固定为 STRUCTURED_FEATURE_COLUMNS）。
        dept_income_sorted: 用于推理时复用的部门薪资分布。
    """
    work = df.copy()

    # 部门薪资分位（按部门内 MonthlyIncome 排名）
    if dept_income_sorted is None:
        dept_income_sorted = {
            str(d): np.sort(work.loc[work["Department"].astype(str) == d, "MonthlyIncome"].to_numpy())
            for d in DEPARTMENTS
        }

    def _pct(row):
        arr = dept_income_sorted.get(str(row["Department"]))
        if arr is None or len(arr) == 0:
            return 0.5
        pos = np.searchsorted(arr, row["MonthlyIncome"], side="right")
        return float(pos) / len(arr)

    work["salary_percentile"] = work.apply(_pct, axis=1)
    work["promotion_gap_months"] = work["YearsSinceLastPromotion"].astype(float)
    work["tenure_ratio"] = work["YearsAtCompany"] / (work["TotalWorkingYears"] + 1.0)

    # 编码分类变量
    work["overtime_flag"] = (work["OverTime"].astype(str) == "Yes").astype(int)
    work["business_travel_ord"] = work["BusinessTravel"].astype(str).map(BUSINESS_TRAVEL_ORD).fillna(1).astype(int)
    for d in DEPARTMENTS:
        work[f"dept_{_safe_col_name(d)}"] = (work["Department"].astype(str) == d).astype(int)
    for m in MARITAL_STATUSES:
        work[f"marital_{m}"] = (work["MaritalStatus"].astype(str) == m).astype(int)

    X = work[STRUCTURED_FEATURE_COLUMNS].copy()
    return X, dept_income_sorted


def engineer_behavior(behavior_df: pd.DataFrame) -> pd.DataFrame:
    """对 12 个月行为时序提取统计特征.

    每个 series 生成 4 个特征：trend_slope / mean / std / recent_change_rate。
    """
    x = np.arange(1, N_MONTHS + 1, dtype=float)
    x_centered = x - x.mean()
    x_ss = float((x_centered ** 2).sum())  # 143.0

    feats: dict[str, np.ndarray] = {}
    for prefix in BEHAVIOR_SERIES:
        cols = [f"{prefix}_month_{m}" for m in range(1, N_MONTHS + 1)]
        values = behavior_df[cols].to_numpy(dtype=float)  # (n, 12)
        y_mean = values.mean(axis=1, keepdims=True)
        # 线性回归斜率：sum((x-x̄)(y-ȳ)) / sum((x-x̄)²)
        slope = (x_centered * (values - y_mean)).sum(axis=1) / x_ss
        mean = values.mean(axis=1)
        std = values.std(axis=1)
        first9 = values[:, :9].mean(axis=1)
        last3 = values[:, 9:].mean(axis=1)
        change_rate = (last3 - first9) / (first9 + 1e-6)
        feats[f"{prefix}_trend_slope"] = slope
        feats[f"{prefix}_mean"] = mean
        feats[f"{prefix}_std"] = std
        feats[f"{prefix}_recent_change_rate"] = change_rate

    return pd.DataFrame(feats)


def build_features() -> dict:
    """从 raw 读取数据，构造特征 + 划分 + 保存到 processed/.

    Returns:
        含划分后各集合与元数据的 dict（便于直接调用）。
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    structured = pd.read_csv(RAW_DIR / "structured_train.csv")
    behavior = pd.read_csv(RAW_DIR / "behavior_train.csv")

    # 校验行数对齐
    assert len(structured) == len(behavior), "结构化与行为数据行数不一致"

    # 全量特征（用全量分布计算薪资分位，保证 train/val/test 一致）
    X_struct_full, dept_income_sorted = engineer_structured(structured)
    X_behav_full = engineer_behavior(behavior)
    y_full = (structured["Attrition"].astype(str) == "Yes").astype(int).to_numpy()
    audit_full = structured[AUDIT_COLUMNS].copy()
    audit_full["__row_idx__"] = np.arange(len(audit_full))

    # 60% train / 20% val / 20% test，分层采样
    idx_all = np.arange(len(structured))
    idx_train, idx_temp = train_test_split(
        idx_all, test_size=(VAL_RATIO + TEST_RATIO), stratify=y_full, random_state=SPLIT_SEED
    )
    val_size_rel = VAL_RATIO / (VAL_RATIO + TEST_RATIO)
    idx_val, idx_test = train_test_split(
        idx_temp, test_size=(1 - val_size_rel), stratify=y_full[idx_temp], random_state=SPLIT_SEED
    )

    def _sel(df, idx):
        return df.iloc[idx].reset_index(drop=True)

    splits = {
        "X_struct_train": _sel(X_struct_full, idx_train),
        "X_struct_val": _sel(X_struct_full, idx_val),
        "X_struct_test": _sel(X_struct_full, idx_test),
        "X_behav_train": _sel(X_behav_full, idx_train),
        "X_behav_val": _sel(X_behav_full, idx_val),
        "X_behav_test": _sel(X_behav_full, idx_test),
        "y_train": pd.Series(y_full[idx_train], name="Attrition"),
        "y_val": pd.Series(y_full[idx_val], name="Attrition"),
        "y_test": pd.Series(y_full[idx_test], name="Attrition"),
        "audit_test": _sel(audit_full, idx_test),
    }

    # 保存为 CSV / npz
    for name, df in splits.items():
        df.to_csv(PROCESSED_DIR / f"{name}.csv", index=False)
    np.savez(
        PROCESSED_DIR / "split_indices.npz",
        train=idx_train, val=idx_val, test=idx_test,
    )

    # 特征元数据（推理时复用）
    metadata = {
        "structured_feature_columns": STRUCTURED_FEATURE_COLUMNS,
        "behavior_feature_columns": list(X_behav_full.columns),
        "dept_income_sorted": dept_income_sorted,
        "business_travel_ord": BUSINESS_TRAVEL_ORD,
        "departments": DEPARTMENTS,
        "marital_statuses": MARITAL_STATUSES,
        "n_train": len(idx_train),
        "n_val": len(idx_val),
        "n_test": len(idx_test),
    }
    with open(MODELS_DIR / "feature_metadata.pkl", "wb") as f:
        pickle.dump(metadata, f)

    print(f"[T-302] 特征工程完成：结构化 {len(STRUCTURED_FEATURE_COLUMNS)} 列，"
          f"行为 {len(X_behav_full.columns)} 列")
    print(f"[T-302] 划分：train={len(idx_train)} val={len(idx_val)} test={len(idx_test)}")
    print(f"[T-302] 训练集离职率：{y_full[idx_train].mean():.2%}")
    print(f"[T-302] 处理后特征保存到 {PROCESSED_DIR}")
    return splits


def load_split() -> dict:
    """加载 processed/ 中的划分数据与元数据."""
    names = [
        "X_struct_train", "X_struct_val", "X_struct_test",
        "X_behav_train", "X_behav_val", "X_behav_test",
        "y_train", "y_val", "y_test", "audit_test",
    ]
    out = {}
    for name in names:
        out[name] = pd.read_csv(PROCESSED_DIR / f"{name}.csv")
    # y 保持 int
    for name in ["y_train", "y_val", "y_test"]:
        out[name] = out[name].iloc[:, 0].astype(int)
    with open(MODELS_DIR / "feature_metadata.pkl", "rb") as f:
        out["metadata"] = pickle.load(f)
    return out


if __name__ == "__main__":
    build_features()
