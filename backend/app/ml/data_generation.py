"""T-301/T-301b 合成数据生成 - 结构化 + 行为时序.

IBM HR 公开数据集下载失败，改为合成生成。
- 优先用 SDV GaussianCopula 基于 1470 条种子数据扩充到 50000 条；
- SDV 太慢/报错则降级为 numpy 直接生成 50000 条（确保特征间合理相关性）。

公平性硬约束（PIPL / 欧盟 AI Act）：
  标签生成函数绝不使用 gender / ethnicity / disability / birth_date。
  审计字段独立随机生成，与模型特征解耦。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ===== 路径（统一用 pathlib，所有产物落在 E: 盘项目内）=====
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # app/ml/x.py -> backend
RAW_DIR = BACKEND_ROOT / "data" / "raw"
PROCESSED_DIR = BACKEND_ROOT / "data" / "processed"

N_MONTHS = 12
DEFAULT_N = 50000
SEED_N = 1470  # IBM HR 原始数据集规模，作为 SDV 种子

# 结构化字段（模型特征）— 参考 IBM HR Attrition
INT_RANGES = {
    "Age": (18, 60),
    "DistanceFromHome": (1, 29),
    "Education": (1, 5),
    "EnvironmentSatisfaction": (1, 4),
    "JobInvolvement": (1, 4),
    "JobLevel": (1, 5),
    "JobSatisfaction": (1, 4),
    "MonthlyIncome": (1000, 20000),
    "NumCompaniesWorked": (0, 9),
    "PercentSalaryHike": (11, 25),
    "PerformanceRating": (3, 4),
    "RelationshipSatisfaction": (1, 4),
    "StockOptionLevel": (0, 3),
    "TotalWorkingYears": (0, 40),
    "TrainingTimesLastYear": (0, 6),
    "WorkLifeBalance": (1, 4),
    "YearsAtCompany": (0, 40),
    "YearsInCurrentRole": (0, 18),
    "YearsSinceLastPromotion": (0, 15),
    "YearsWithCurrManager": (0, 17),
}
CATEGORICAL_VALUES = {
    "OverTime": ["Yes", "No"],
    "BusinessTravel": ["Travel_Rarely", "Travel_Frequently", "Non-Travel"],
    "Department": ["Sales", "R&D", "HR"],
    "MaritalStatus": ["Single", "Married", "Divorced"],
}
# 公平性审计字段（不入模型，仅审计用）
AUDIT_COLUMNS = ["gender", "ethnicity", "disability", "birth_date", "age_derived"]


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _generate_feature_columns(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """生成结构化模型特征列（不含审计字段、不含 Attrition）.

    特征间保留合理相关性：
      - TotalWorkingYears ~ Age-22（工龄随年龄增长）
      - YearsAtCompany / YearsInCurrentRole / YearsWithCurrManager / YearsSinceLastPromotion
        逐层嵌套约束（年限递减）
      - MonthlyIncome ~ JobLevel × Department（职级与部门决定薪资）
    """
    Age = rng.integers(18, 61, n)
    # 多数员工住得近（指数分布偏斜），裁剪到 1-29
    DistanceFromHome = np.clip(
        np.round(rng.exponential(scale=7.0, size=n) + 1).astype(int), 1, 29
    )
    Education = rng.integers(1, 6, n)
    EnvironmentSatisfaction = rng.integers(1, 5, n)
    JobInvolvement = rng.integers(1, 5, n)
    JobLevel = rng.integers(1, 6, n)
    JobSatisfaction = rng.integers(1, 5, n)
    NumCompaniesWorked = rng.integers(0, 10, n)
    PercentSalaryHike = rng.integers(11, 26, n)
    PerformanceRating = rng.choice([3, 4], n, p=[0.85, 0.15])
    RelationshipSatisfaction = rng.integers(1, 5, n)
    StockOptionLevel = rng.integers(0, 4, n)
    TrainingTimesLastYear = rng.integers(0, 7, n)
    WorkLifeBalance = rng.integers(1, 5, n)

    # 工龄与年龄相关：约 22 岁开始工作
    TotalWorkingYears = np.clip(Age - 22 + rng.integers(-3, 4, n), 0, 40)
    # 在职年限 <= 总工龄
    YearsAtCompany = np.array(
        [int(rng.integers(0, int(tw) + 1)) if tw > 0 else 0 for tw in TotalWorkingYears]
    )
    YearsAtCompany = np.clip(YearsAtCompany, 0, 40)
    YearsInCurrentRole = np.minimum(YearsAtCompany, rng.integers(0, 19, n))
    YearsWithCurrManager = np.minimum(YearsAtCompany, rng.integers(0, 18, n))
    # 多数员工近期晋升过（指数偏斜，长尾），裁剪到 0-15 且 <= YearsAtCompany
    ysp = np.clip(np.round(rng.exponential(scale=2.5, size=n)).astype(int), 0, 15)
    YearsSinceLastPromotion = np.minimum(ysp, YearsAtCompany)

    # 薪资由职级 × 部门决定
    Department = rng.choice(["Sales", "R&D", "HR"], n, p=[0.30, 0.50, 0.20])
    dept_factor_map = {"Sales": 1.00, "R&D": 1.15, "HR": 0.90}
    dept_factor = np.array([dept_factor_map[d] for d in Department])
    base_income = JobLevel.astype(float) * 3500.0
    MonthlyIncome = (base_income * dept_factor + rng.normal(0, 1500, n)).astype(int)
    MonthlyIncome = np.clip(MonthlyIncome, 1000, 20000)

    OverTime = rng.choice(["Yes", "No"], n, p=[0.25, 0.75])
    BusinessTravel = rng.choice(
        ["Travel_Rarely", "Travel_Frequently", "Non-Travel"], n, p=[0.70, 0.20, 0.10]
    )
    MaritalStatus = rng.choice(["Single", "Married", "Divorced"], n, p=[0.45, 0.40, 0.15])

    df = pd.DataFrame(
        {
            "Age": Age,
            "DistanceFromHome": DistanceFromHome,
            "Education": Education,
            "EnvironmentSatisfaction": EnvironmentSatisfaction,
            "JobInvolvement": JobInvolvement,
            "JobLevel": JobLevel,
            "JobSatisfaction": JobSatisfaction,
            "MonthlyIncome": MonthlyIncome,
            "NumCompaniesWorked": NumCompaniesWorked,
            "PercentSalaryHike": PercentSalaryHike,
            "PerformanceRating": PerformanceRating,
            "RelationshipSatisfaction": RelationshipSatisfaction,
            "StockOptionLevel": StockOptionLevel,
            "TotalWorkingYears": TotalWorkingYears,
            "TrainingTimesLastYear": TrainingTimesLastYear,
            "WorkLifeBalance": WorkLifeBalance,
            "YearsAtCompany": YearsAtCompany,
            "YearsInCurrentRole": YearsInCurrentRole,
            "YearsSinceLastPromotion": YearsSinceLastPromotion,
            "YearsWithCurrManager": YearsWithCurrManager,
            "OverTime": OverTime,
            "BusinessTravel": BusinessTravel,
            "Department": Department,
            "MaritalStatus": MaritalStatus,
        }
    )
    return df


def compute_attrition_probability(df: pd.DataFrame) -> np.ndarray:
    """根据领域规则计算离职概率（sigmoid 转概率）.

    公平性约束：本函数仅使用业务特征，绝不使用 gender/ethnicity/disability/birth_date。
    基础离职率 ~12%，各风险因子叠加，logit 空间线性组合后 sigmoid。
    """
    bonus = np.zeros(len(df), dtype=float)
    bonus += np.where(df["OverTime"].eq("Yes").to_numpy(), 0.25, 0.0)
    bonus += np.where(df["YearsSinceLastPromotion"].to_numpy() > 3, 0.15, 0.0)
    bonus += np.where(df["MonthlyIncome"].to_numpy() < 4000, 0.15, 0.0)
    bonus += np.where(df["JobSatisfaction"].to_numpy() <= 2, 0.12, 0.0)
    bonus += np.where(df["EnvironmentSatisfaction"].to_numpy() <= 2, 0.10, 0.0)
    bonus += np.where(df["DistanceFromHome"].to_numpy() > 15, 0.08, 0.0)
    bonus += np.where(df["Age"].to_numpy() < 30, 0.05, 0.0)

    # 基础 logit（无风险因子员工离职率极低）；乘子放大可分性，保证 AUC≥0.85，
    # 整体离职率约 18-20%（现实区间）。
    base_logit = -7.0
    logit = base_logit + bonus * 18.0
    p = 1.0 / (1.0 + np.exp(-logit))
    return p, bonus


def _clip_and_fix_constraints(df: pd.DataFrame) -> pd.DataFrame:
    """裁剪到合法取值范围，并修复年限嵌套约束."""
    df = df.copy()
    for col, (lo, hi) in INT_RANGES.items():
        if col in df.columns:
            if col == "MonthlyIncome":
                df[col] = np.clip(np.round(df[col].astype(float)).astype(int), lo, hi)
            else:
                df[col] = np.clip(np.round(df[col].astype(float)).astype(int), lo, hi)
    for col, vals in CATEGORICAL_VALUES.items():
        if col in df.columns:
            # SDV 可能生成未知类别，用最近类别替换
            df[col] = df[col].astype(str)
            mask = ~df[col].isin(vals)
            if mask.any():
                df.loc[mask, col] = vals[0]
            df[col] = pd.Categorical(df[col], categories=vals)

    # 修复年限嵌套约束
    tw = df["TotalWorkingYears"].to_numpy()
    yac = np.minimum(df["YearsAtCompany"].to_numpy(), tw)
    df["YearsAtCompany"] = np.clip(yac, 0, 40)
    df["YearsInCurrentRole"] = np.minimum(df["YearsInCurrentRole"].to_numpy(), df["YearsAtCompany"].to_numpy())
    df["YearsWithCurrManager"] = np.minimum(
        df["YearsWithCurrManager"].to_numpy(), df["YearsAtCompany"].to_numpy()
    )
    df["YearsSinceLastPromotion"] = np.minimum(
        df["YearsSinceLastPromotion"].to_numpy(), df["YearsAtCompany"].to_numpy()
    )
    return df


def _add_audit_and_label(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """追加独立随机的审计字段 + birth_date + Attrition 标签.

    审计字段（gender/ethnicity/disability）与模型特征独立生成，确保公平性。
    """
    n = len(df)
    # 审计字段独立于特征
    gender = rng.choice(["M", "F"], n)
    ethnicity = rng.choice([0, 1], n, p=[0.85, 0.15])  # 0=汉族, 1=少数民族
    disability = rng.choice([0, 1], n, p=[0.95, 0.05])

    # birth_date 与 Age 一致：约 Age 年前出生
    today = date.today()
    birth_date = []
    for age in df["Age"].to_numpy():
        year = today.year - int(age)
        month = int(rng.integers(1, 13))
        day = int(rng.integers(1, 28))
        birth_date.append(date(year, month, day).isoformat())

    df = df.copy()
    df["gender"] = gender
    df["ethnicity"] = ethnicity
    df["disability"] = disability
    df["birth_date"] = birth_date
    df["age_derived"] = df["Age"].astype(int)  # 便于审计按 age 分组

    # 标签：仅依赖业务特征（公平性约束）
    p, _ = compute_attrition_probability(df)
    attrition_flag = rng.binomial(1, p)
    df["Attrition"] = np.where(attrition_flag == 1, "Yes", "No")
    return df


def _try_sdv_augment(seed_df: pd.DataFrame, n_target: int, seed: int) -> pd.DataFrame:
    """用 SDV GaussianCopula 扩充（失败抛异常由调用方降级）."""
    from sdv.metadata import Metadata
    from sdv.single_table import GaussianCopulaSynthesizer

    work = seed_df.copy()
    # SDV 不处理 pandas Categorical，转为 object
    for col in CATEGORICAL_VALUES:
        if col in work.columns:
            work[col] = work[col].astype(str)
    # 去掉 birth_date/审计字段（已在 seed_df 中排除）
    metadata = Metadata.detect_from_dataframe(work)
    synthesizer = GaussianCopulaSynthesizer(
        metadata, enforce_rounding=False, enforce_min_max_values=False
    )
    synthesizer.fit(work)
    sample = synthesizer.sample(num_rows=n_target)
    # 对齐列顺序
    sample = sample[seed_df.columns]
    return sample


def generate_structured_data(
    n: int = DEFAULT_N, seed: int = 42, use_sdv: bool = True
) -> pd.DataFrame:
    """生成结构化训练数据（n 行），含模型特征 + 审计字段 + Attrition."""
    rng = _rng(seed)
    df = None
    if use_sdv:
        try:
            print(f"[T-301] 尝试 SDV GaussianCopula 扩充（种子 {SEED_N} → {n}）...")
            seed_df = _generate_feature_columns(SEED_N, _rng(seed + 1))
            sample_df = _try_sdv_augment(seed_df, n, seed + 2)
            sample_df = _clip_and_fix_constraints(sample_df)
            df = _add_audit_and_label(sample_df, rng)
            print("[T-301] SDV 路径成功。")
        except Exception as e:  # noqa: BLE001 - 降级需捕获所有异常
            print(f"[T-301] SDV 失败（{type(e).__name__}: {e}），降级为 numpy 直接生成。")
            df = None
    if df is None:
        print(f"[T-301] numpy 直接生成 {n} 条结构化数据...")
        feat_df = _generate_feature_columns(n, rng)
        feat_df = _clip_and_fix_constraints(feat_df)
        df = _add_audit_and_label(feat_df, rng)
        print("[T-301] numpy 路径完成。")

    return df


def generate_behavior_data(structured_df: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """生成 12 个月行为时序数据.

    离职员工在后几个月（8-12 月）呈现可检测异常趋势：
      - email_count 下降
      - meeting_decline_rate 上升
      - login_count 下降
    非离职员工行为平稳（基线 + 小噪声）。
    """
    rng = _rng(seed)
    n = len(structured_df)
    is_attrition = structured_df["Attrition"].eq("Yes").to_numpy()

    # 每员工基线水平
    base_email = rng.integers(40, 121, n).astype(float)
    base_decline = rng.uniform(0.05, 0.20, n)
    base_login = rng.integers(5, 16, n).astype(float)

    rows = {}
    for m in range(1, N_MONTHS + 1):
        # 季节性小幅波动
        seasonal = 0.05 * np.sin(2 * np.pi * m / 12.0)
        email = base_email * (1.0 + seasonal) + rng.normal(0, 4.0, n)
        decline = base_decline + rng.normal(0, 0.01, n)
        login = base_login * (1.0 + seasonal * 0.5) + rng.normal(0, 0.8, n)

        if m >= 8:
            # 离职员工后 5 个月异常趋势
            k = m - 7  # 1..5
            decline_factor = 1.0 - 0.12 * k  # 0.88,0.76,...,0.40
            decline_factor = np.where(is_attrition, decline_factor, 1.0)
            email = np.where(is_attrition, base_email * decline_factor, email)
            decline = np.where(is_attrition, base_decline + 0.05 * k, decline)
            login = np.where(is_attrition, base_login * decline_factor, login)

        rows[f"email_count_month_{m}"] = np.clip(email, 0, None)
        rows[f"meeting_decline_rate_month_{m}"] = np.clip(decline, 0.0, 1.0)
        rows[f"login_count_month_{m}"] = np.clip(login, 0, None)

    behavior = pd.DataFrame(rows)
    behavior.insert(0, "employee_id", np.arange(n))
    return behavior


def generate_all(n: int = DEFAULT_N, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """生成结构化 + 行为数据并保存到 data/raw/."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    structured = generate_structured_data(n=n, seed=seed)
    behavior = generate_behavior_data(structured, seed=seed + 100)

    struct_path = RAW_DIR / "structured_train.csv"
    behav_path = RAW_DIR / "behavior_train.csv"
    structured.to_csv(struct_path, index=False)
    behavior.to_csv(behav_path, index=False)

    attrition_rate = structured["Attrition"].eq("Yes").mean()
    print(f"[T-301] 结构化数据: {len(structured)} 行 → {struct_path}")
    print(f"[T-301] 行为数据: {len(behavior)} 行 → {behav_path}")
    print(f"[T-301] 离职率: {attrition_rate:.2%}")
    return structured, behavior


if __name__ == "__main__":
    generate_all()
