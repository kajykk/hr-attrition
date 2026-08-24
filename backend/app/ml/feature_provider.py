"""特征提供器 - 将 Employee ORM 转换为模型输入特征（W4 集成）.

职责：
  1. build_structured_features(employee) → 31 列结构化特征 DataFrame
  2. build_behavior_features(employee) → 12 列行为统计特征 DataFrame
  3. build_features(employee) → (structured, behavior) 元组

公平性硬约束：gender/ethnicity/disability/birth_date 永不出现在模型特征中。

P0-4 特征真实化：
  - 结构化特征改为「真实字段优先」：Employee 模型新增真实特征字段
    （distance_from_home 等，0002 迁移），非空时直接使用
  - 缺失（None）时回退为训练分布中位/众数常量（_FEATURE_DEFAULTS），
    **不再使用确定性伪随机数**，消除"预测由随机数主导"
  - salary_percentile 统一为 0-1 分位：DB 存 0-100，推理乘 SALARY_PERCENTILE_SCALE
  - 行为特征：当前无真实行为采集通道，保留特征工程同分布构造（演示模式），
    生产接入真实时序后应替换为真实数据（见 README 说明）

特征契约（与 T-302 训练侧对齐，防推理/训练定义漂移）：
  - 列名与列顺序必须等于训练元数据 feature_metadata.pkl 中 structured_feature_columns。
  一致性由 assert_feature_contract() 校验（P1-5 修复）。
"""
from __future__ import annotations

import pickle
import re
from pathlib import Path

import pandas as pd

from app.core.timeutil import today
from app.ml.feature_engineering import (
    BEHAVIOR_SERIES,
    DEPARTMENTS,
    MARITAL_STATUSES,
    N_MONTHS,
    STRUCTURED_FEATURE_COLUMNS,
)

# DB 百分位（0-100）→ 模型输入分位（0-1）的换算系数（显式契约，训练侧同理）
SALARY_PERCENTILE_SCALE = 0.01

# 行为模态数据来源标识：当前无真实行为采集通道，行为时序为 demo 数据
# （由 employee.id 播种确定性生成），风险预测 API 响应以该值标注来源；
# 接入真实行为日志后应改为 "real"（路线图项，见 README）。
BEHAVIOR_DATA_SOURCE_DEMO = "demo"

_MODELS_DIR = Path(__file__).resolve().parent / "models"
_FEATURE_METADATA_PATH = _MODELS_DIR / "feature_metadata.pkl"
_training_metadata_cache: dict | None = None

# ===== P0-4 缺失占位：训练分布中位/众数（IBM 合成训练集典型值） =====
# 真实字段缺失时使用这些常量，而非随机数，保证确定性 + 贴近训练分布。
_FEATURE_DEFAULTS: dict[str, object] = {
    "distance_from_home": 9,
    "education": 3,
    "environment_satisfaction": 3,
    "job_involvement": 3,
    "job_level": 2,
    "job_satisfaction": 3,
    "num_companies_worked": 2,
    "percent_salary_hike": 14,
    "performance_rating": 3,
    "relationship_satisfaction": 3,
    "stock_option_level": 1,
    "total_working_years": 10,
    "training_times_last_year": 2,
    "work_life_balance": 3,
    "years_in_current_role": 3,
    "years_since_last_promotion": 1,
    "years_with_curr_manager": 3,
    "overtime": 0,
    "business_travel": "Travel_Rarely",
    "marital_status": "Married",
}


def _attr(employee, name, default=None):
    """读取 ORM 属性，缺失时返回 default（兼容旧对象/测试替身，不抛错）."""
    return getattr(employee, name, default)


def _int_or_default(employee, field_name: str, lo: int, hi: int) -> int:
    """读取整型特征并钳位到 [lo, hi]；缺失/非法时返回训练分布占位常量."""
    default = int(_FEATURE_DEFAULTS[field_name])
    val = _attr(employee, field_name)
    if val is None:
        return default
    try:
        return min(hi, max(lo, int(val)))
    except (TypeError, ValueError):
        return default


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
    pos = (_attr(employee, "position") or "").lower()
    for kw, dept in _POSITION_DEPT_HINTS.items():
        if kw in pos:
            return dept
    # 默认 Sales（数据集中占比最高）
    return "Sales"


def _age_from_birth(birth_date) -> int:
    """从出生日期计算年龄."""
    if birth_date is None:
        return 35  # 默认中位数
    today_d = today()
    age = today_d.year - birth_date.year
    if (today_d.month, today_d.day) < (birth_date.month, birth_date.day):
        age -= 1
    return max(18, min(60, age))


def _years_at_company(hire_date) -> int:
    """从入职日期计算在司年数."""
    if hire_date is None:
        return 5
    today_d = today()
    years = today_d.year - hire_date.year
    if (today_d.month, today_d.day) < (hire_date.month, hire_date.day):
        years -= 1
    return max(0, min(40, years))


def _salary_percentile_value(employee) -> float:
    """从 employee.salary_percentile（0-100 百分位 Decimal）读取，转为 0-1 浮点.

    契约：DB 存 0-100 百分位，乘以 SALARY_PERCENTILE_SCALE（0.01）得到与训练侧
    （部门内分位 0-1）同量纲的模型输入。
    """
    sp = _attr(employee, "salary_percentile")
    if sp is None:
        return 0.5
    try:
        return min(1.0, max(0.0, float(sp) * SALARY_PERCENTILE_SCALE))
    except (TypeError, ValueError):
        return 0.5


def _monthly_income(employee) -> float:
    """月薪：优先解析 salary 加密字段（Fernet 解密后提取数值），失败回退分位估算."""
    enc = _attr(employee, "salary_encrypted")
    if enc:
        try:
            from app.core.security import decrypt_pii

            plain = decrypt_pii(enc)
            if plain:
                m = re.search(r"\d+(?:\.\d+)?", plain)
                if m:
                    val = float(m.group(0))
                    if 1000 <= val <= 1000000:
                        return val
        except (ValueError, TypeError):
            pass
    # 从分位反推（训练侧 MonthlyIncome 量纲参考）
    return _salary_percentile_value(employee) * 20000.0


def _business_travel_ord(employee) -> int:
    """出差频率 → 0/1/2（对齐训练侧 BUSINESS_TRAVEL_ORD）."""
    mapping = {"Non-Travel": 0, "Travel_Rarely": 1, "Travel_Frequently": 2}
    val = _attr(employee, "business_travel")
    if val is None:
        return 1  # 训练分布众数 Travel_Rarely
    return mapping.get(str(val).strip(), 1)


def _marital_status(employee) -> str:
    """婚姻状况：真实值必须是训练枚举；缺失/非法回退众数 Married."""
    val = _attr(employee, "marital_status")
    if val is None:
        return str(_FEATURE_DEFAULTS["marital_status"])
    v = str(val)
    return v if v in MARITAL_STATUSES else str(_FEATURE_DEFAULTS["marital_status"])


def load_training_metadata() -> dict | None:
    """懒加载训练侧特征元数据（feature_metadata.pkl）.

    Returns:
        元数据 dict；元数据文件缺失时返回 None（特征仍可构造，但跳过契约校验）。
    """
    global _training_metadata_cache
    if _training_metadata_cache is not None:
        return _training_metadata_cache
    if not _FEATURE_METADATA_PATH.exists():
        return None
    try:
        with open(_FEATURE_METADATA_PATH, "rb") as f:
            _training_metadata_cache = pickle.load(f)
    except (pickle.UnpicklingError, EOFError, OSError):
        return None
    return _training_metadata_cache


def assert_feature_contract() -> None:
    """校验推理侧特征契约与训练侧一致（列名 + 顺序）.

    Raises:
        RuntimeError: 元数据存在但列定义不一致（防推理/训练特征漂移）。
    """
    metadata = load_training_metadata()
    if metadata is None:
        # 未训练/未生成元数据时无法校验，跳过（显式契约仍由测试覆盖）
        return
    trained_struct = list(metadata.get("structured_feature_columns", []))
    if trained_struct and trained_struct != list(STRUCTURED_FEATURE_COLUMNS):
        raise RuntimeError(
            "特征契约不一致：训练侧 structured_feature_columns 与推理侧 "
            "STRUCTURED_FEATURE_COLUMNS 不同"
        )
    trained_behav = list(metadata.get("behavior_feature_columns", []))
    expected_behav = sorted(
        f"{p}_{s}" for p in BEHAVIOR_SERIES
        for s in ("trend_slope", "mean", "std", "recent_change_rate")
    )
    if trained_behav and sorted(trained_behav) != expected_behav:
        raise RuntimeError("特征契约不一致：行为特征列与训练侧不同")


def build_structured_features(employee) -> pd.DataFrame:
    """构造单员工的结构化特征 DataFrame（31 列，顺序固定）.

    P0-4：真实字段优先（Employee 新特征字段），缺失时回退训练分布中位常量，
    不再使用确定性伪随机数。

    Args:
        employee: Employee ORM 实例。

    Returns:
        单行 DataFrame，列顺序 = STRUCTURED_FEATURE_COLUMNS。
    """
    # ===== 派生自 ORM 的字段（真实可推导） =====
    age = _age_from_birth(_attr(employee, "birth_date"))
    years_at_company = _years_at_company(_attr(employee, "hire_date"))
    salary_pct = _salary_percentile_value(employee)
    monthly_income = _monthly_income(employee)

    # ===== 真实字段优先，缺失 → 训练分布中位/众数常量 =====
    distance_from_home = _int_or_default(employee, "distance_from_home", 1, 30)
    education = _int_or_default(employee, "education", 1, 5)
    environment_satisfaction = _int_or_default(employee, "environment_satisfaction", 1, 4)
    job_involvement = _int_or_default(employee, "job_involvement", 1, 4)
    job_level = _int_or_default(employee, "job_level", 1, 5)
    job_satisfaction = _int_or_default(employee, "job_satisfaction", 1, 4)
    num_companies_worked = _int_or_default(employee, "num_companies_worked", 0, 10)
    percent_salary_hike = _int_or_default(employee, "percent_salary_hike", 11, 25)
    performance_rating = _int_or_default(employee, "performance_rating", 3, 4)
    relationship_satisfaction = _int_or_default(employee, "relationship_satisfaction", 1, 4)
    stock_option_level = _int_or_default(employee, "stock_option_level", 0, 3)
    total_working_years = _int_or_default(employee, "total_working_years", 0, 40)
    training_times_last_year = _int_or_default(employee, "training_times_last_year", 0, 6)
    work_life_balance = _int_or_default(employee, "work_life_balance", 1, 4)
    years_in_current_role = _int_or_default(employee, "years_in_current_role", 0, 19)
    years_since_last_promotion = _int_or_default(employee, "years_since_last_promotion", 0, 16)
    years_with_curr_manager = _int_or_default(employee, "years_with_curr_manager", 0, 18)

    overtime_val = _attr(employee, "overtime")
    overtime_flag = int(bool(overtime_val)) if overtime_val is not None else int(_FEATURE_DEFAULTS["overtime"])
    business_travel_ord = _business_travel_ord(employee)

    # ===== 部门 / 婚姻 one-hot =====
    dept = _infer_department(employee)
    marital = _marital_status(employee)

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

    ⚠️ 演示模式（P0-4）：当前无真实行为采集通道（无邮件/会议/登录日志表），
    按训练侧同分布构造确定性时序，仅供演示。生产环境接入真实行为数据后，
    应改为读取真实 12 月时序（保留相同的统计量提取逻辑）。

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
    import numpy as np

    seed = int.from_bytes(str(_attr(employee, "id", "unknown")).encode("utf-8")[:8], "big") % (2**31)
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


def build_features(employee) -> tuple[pd.DataFrame, pd.DataFrame]:
    """构造员工完整特征（结构化 + 行为）.

    Args:
        employee: Employee ORM 实例。

    Returns:
        (structured_df, behavior_df) 元组，均为单行 DataFrame。
    """
    return build_structured_features(employee), build_behavior_features(employee)
