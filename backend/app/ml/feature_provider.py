"""特征提供器 - 将 Employee ORM 转换为模型输入特征（W4 集成）.

职责：
  1. build_structured_features(employee) → 31 列结构化特征 DataFrame
  2. build_behavior_features(employee) → 12 列行为统计特征 DataFrame
  3. build_features(employee) → (structured, behavior) 元组

公平性硬约束：gender/ethnicity/disability/birth_date 永不出现在模型特征中。
缺失字段使用基于 employee.id 的确定性伪随机生成（hashlib 种子），确保同一员工每次得到相同特征。
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Tuple

import numpy as np
import pandas as pd

from app.ml.feature_engineering import (
    BEHAVIOR_SERIES,
    DEPARTMENTS,
    MARITAL_STATUSES,
    N_MONTHS,
    STRUCTURED_FEATURE_COLUMNS,
)


# ===== 部门名称映射（ORM 无 department 名称，用 position/level 推断） =====
# 简化策略：根据 position 字符串关键字推断部门；无 position 时默认 Sales
_POSITION_DEPT_HINTS = {
    "sales": "Sales",
    "rd": "R&D",
    "research": "R&D",
    "开发": "R&D",
    "hr": "HR",
    "人力": "HR",
}


def _infer_department(employee) -> str:
    """从 employee.position 推断部门名称（用于 one-hot 编码）."""
    pos = (employee.position or "").lower()
    for kw, dept in _POSITION_DEPT_HINTS.items():
        if kw in pos:
            return dept
    # 默认 Sales（数据集中占比最高）
    return "Sales"


def _infer_marital_status(seed: int) -> str:
    """基于种子确定性选择婚姻状态."""
    rng = np.random.RandomState(seed)
    idx = int(rng.randint(0, len(MARITAL_STATUSES)))
    return MARITAL_STATUSES[idx]


def _deterministic_seed(employee_id) -> int:
    """从 employee.id 生成稳定的 32 位整数种子（同一员工每次相同）."""
    raw = str(employee_id).encode("utf-8")
    h = hashlib.sha256(raw).digest()
    # 取前 4 字节作为 uint32 种子
    return int.from_bytes(h[:4], "big") & 0x7FFFFFFF


def _age_from_birth(birth_date) -> int:
    """从出生日期计算年龄."""
    if birth_date is None:
        return 35  # 默认中位数
    today = date.today()
    age = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        age -= 1
    return max(18, min(60, age))


def _years_at_company(hire_date) -> int:
    """从入职日期计算在司年数."""
    if hire_date is None:
        return 5
    today = date.today()
    years = today.year - hire_date.year
    if (today.month, today.day) < (hire_date.month, hire_date.day):
        years -= 1
    return max(0, min(40, years))


def _salary_percentile_value(employee) -> float:
    """从 employee.salary_percentile（Decimal）读取，转为 0-1 浮点."""
    sp = employee.salary_percentile
    if sp is None:
        return 0.5
    try:
        return float(sp) / 100.0
    except (TypeError, ValueError):
        return 0.5


def build_structured_features(employee) -> pd.DataFrame:
    """构造单员工的结构化特征 DataFrame（31 列，顺序固定）.

    Args:
        employee: Employee ORM 实例。

    Returns:
        单行 DataFrame，列顺序 = STRUCTURED_FEATURE_COLUMNS。
    """
    seed = _deterministic_seed(employee.id)
    rng = np.random.RandomState(seed)

    # ===== 派生自 ORM 的字段 =====
    age = _age_from_birth(employee.birth_date)
    years_at_company = _years_at_company(employee.hire_date)
    salary_pct = _salary_percentile_value(employee)
    monthly_income = salary_pct * 20000.0  # 从分位反推月薪

    # ===== 确定性伪随机字段（模拟 HR 系统数据快照） =====
    distance_from_home = int(rng.randint(1, 30))
    education = int(rng.randint(1, 6))
    environment_satisfaction = int(rng.randint(1, 5))
    job_involvement = int(rng.randint(1, 5))
    job_level = int(rng.randint(1, 6))
    job_satisfaction = int(rng.randint(1, 5))
    num_companies_worked = int(rng.randint(0, 10))
    percent_salary_hike = int(rng.randint(11, 26))
    performance_rating = int(rng.choice([3, 4]))
    relationship_satisfaction = int(rng.randint(1, 5))
    stock_option_level = int(rng.randint(0, 4))
    total_working_years = int(rng.randint(max(years_at_company, 0), 41))
    training_times_last_year = int(rng.randint(0, 7))
    work_life_balance = int(rng.randint(1, 5))
    years_in_current_role = int(rng.randint(0, min(years_at_company + 1, 19)))
    years_since_last_promotion = int(rng.randint(0, 16))
    years_with_curr_manager = int(rng.randint(0, min(years_at_company + 1, 18)))

    overtime_flag = int(rng.randint(0, 2))
    business_travel_ord = int(rng.randint(0, 3))

    # ===== 部门 / 婚姻 one-hot =====
    dept = _infer_department(employee)
    marital = _infer_marital_status(seed + 1)

    dept_onehot = {f"dept_{d.replace('&', '').replace(' ', '_')}": int(dept == d) for d in DEPARTMENTS}
    marital_onehot = {f"marital_{m}": int(marital == m) for m in MARITAL_STATUSES}

    # ===== 派生特征 =====
    promotion_gap_months = float(years_since_last_promotion)
    tenure_ratio = float(years_at_company) / max(float(total_working_years), 1.0)

    row = {
        "Age": age,
        "DistanceFromHome": distance_from_home,
        "Education": education,
        "EnvironmentSatisfaction": environment_satisfaction,
        "JobInvolvement": job_involvement,
        "JobLevel": job_level,
        "JobSatisfaction": job_satisfaction,
        "MonthlyIncome": monthly_income,
        "NumCompaniesWorked": num_companies_worked,
        "PercentSalaryHike": percent_salary_hike,
        "PerformanceRating": performance_rating,
        "RelationshipSatisfaction": relationship_satisfaction,
        "StockOptionLevel": stock_option_level,
        "TotalWorkingYears": total_working_years,
        "TrainingTimesLastYear": training_times_last_year,
        "WorkLifeBalance": work_life_balance,
        "YearsAtCompany": years_at_company,
        "YearsInCurrentRole": years_in_current_role,
        "YearsSinceLastPromotion": years_since_last_promotion,
        "YearsWithCurrManager": years_with_curr_manager,
        "overtime_flag": overtime_flag,
        "business_travel_ord": business_travel_ord,
        **dept_onehot,
        **marital_onehot,
        "salary_percentile": salary_pct,
        "promotion_gap_months": promotion_gap_months,
        "tenure_ratio": tenure_ratio,
    }

    # 按 STRUCTURED_FEATURE_COLUMNS 顺序构造单行 DataFrame
    df = pd.DataFrame([[row[c] for c in STRUCTURED_FEATURE_COLUMNS]], columns=STRUCTURED_FEATURE_COLUMNS)
    return df


def build_behavior_features(employee) -> pd.DataFrame:
    """构造单员工的行为统计特征 DataFrame（12 列 = 3 指标 × 4 统计量）.

    行为指标：email_count / meeting_decline_rate / login_count
    每个指标生成 12 个月时序，再提取：
      - trend_slope（线性回归斜率）
      - mean（均值）
      - std（标准差）
      - recent_change_rate（近 3 月 vs 前 9 月变化率）

    Args:
        employee: Employee ORM 实例。

    Returns:
        单行 DataFrame，列名为 {prefix}_{stat}。
    """
    seed = _deterministic_seed(employee.id)
    rng = np.random.RandomState(seed + 1000)

    # 生成 12 个月时序（每个指标 1×12）
    series_data: dict[str, np.ndarray] = {}
    for prefix in BEHAVIOR_SERIES:
        if prefix == "email_count":
            base = float(rng.randint(50, 200))
            series_data[prefix] = base + rng.normal(0, 10, N_MONTHS).cumsum() * 0.3
        elif prefix == "meeting_decline_rate":
            base = float(rng.uniform(0.05, 0.25))
            series_data[prefix] = np.clip(
                base + rng.normal(0, 0.02, N_MONTHS).cumsum() * 0.005, 0.0, 1.0
            )
        else:  # login_count
            base = float(rng.randint(20, 80))
            series_data[prefix] = base + rng.normal(0, 5, N_MONTHS).cumsum() * 0.2

    # 提取 4 个统计量（复用 feature_engineering.engineer_behavior 逻辑）
    x = np.arange(1, N_MONTHS + 1, dtype=float)
    x_centered = x - x.mean()
    x_ss = float((x_centered ** 2).sum())  # 143.0

    feats: dict[str, float] = {}
    for prefix in BEHAVIOR_SERIES:
        values = series_data[prefix]  # (12,)
        y_mean = values.mean()
        slope = float((x_centered * (values - y_mean)).sum() / x_ss)
        mean = float(values.mean())
        std = float(values.std())
        first9 = values[:9].mean()
        last3 = values[9:].mean()
        change_rate = float((last3 - first9) / (first9 + 1e-6))
        feats[f"{prefix}_trend_slope"] = slope
        feats[f"{prefix}_mean"] = mean
        feats[f"{prefix}_std"] = std
        feats[f"{prefix}_recent_change_rate"] = change_rate

    return pd.DataFrame([feats])


def build_features(employee) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """构造员工完整特征（结构化 + 行为）.

    Args:
        employee: Employee ORM 实例。

    Returns:
        (structured_df, behavior_df) 元组，均为单行 DataFrame。
    """
    return build_structured_features(employee), build_behavior_features(employee)
