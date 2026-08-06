"""W4 集成测试 - feature_provider / pii_crypto / RiskService / WarningService 申诉转换.

覆盖：
  - feature_provider 确定性（同一 Employee 两次生成特征完全相同）
  - pii_crypto encrypt → decrypt 往返一致；hash_field 稳定
  - RiskService.score_to_level 各阈值
  - WarningService 申诉转换（new→confirmed→appealing 合法；closed→appealing 非法）
  - SHAP 端点返回 Top3 factors（模型文件存在时；否则 skipif 跳过）
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.core import pii_crypto
from app.ml.feature_engineering import STRUCTURED_FEATURE_COLUMNS
from app.models.warning import (
    LEVEL_P1,
    STATUS_APPEALING,
    STATUS_CLOSED,
    STATUS_CONFIRMED,
    STATUS_NEW,
)
from app.services.risk_service import RiskService
from app.services.warning_service import WarningService

# ===== 测试用 Employee 替身（避免依赖 DB） =====


@dataclass
class _FakeEmployee:
    """Employee ORM 替身，仅含 feature_provider 所需字段."""

    id: object
    birth_date: date | None = None
    hire_date: date | None = None
    salary_percentile: Decimal | None = None
    position: str | None = None
    level: str | None = None
    # ===== P0-4 真实特征字段（可空 → 特征层回退训练分布占位） =====
    distance_from_home: int | None = None
    education: int | None = None
    environment_satisfaction: int | None = None
    job_involvement: int | None = None
    job_level: int | None = None
    job_satisfaction: int | None = None
    num_companies_worked: int | None = None
    percent_salary_hike: int | None = None
    performance_rating: int | None = None
    relationship_satisfaction: int | None = None
    stock_option_level: int | None = None
    total_working_years: int | None = None
    training_times_last_year: int | None = None
    work_life_balance: int | None = None
    years_in_current_role: int | None = None
    years_since_last_promotion: int | None = None
    years_with_curr_manager: int | None = None
    overtime: bool | None = None
    business_travel: str | None = None
    marital_status: str | None = None


def _make_employee() -> _FakeEmployee:
    """构造一个测试用员工（固定字段值）."""
    return _FakeEmployee(
        id=uuid4(),
        birth_date=date(1990, 5, 15),
        hire_date=date(2018, 3, 1),
        salary_percentile=Decimal("72.50"),
        position="Senior Sales Engineer",
        level="P6",
    )


# ===== 1. feature_provider 确定性测试 =====


def test_feature_provider_deterministic_same_employee():
    """同一 Employee 两次生成特征应完全相同（确定性伪随机）."""
    from app.ml.feature_provider import build_features

    emp = _make_employee()
    structured_1, behavior_1 = build_features(emp)
    structured_2, behavior_2 = build_features(emp)

    # 结构化特征 DataFrame 完全一致
    import pandas as pd

    pd.testing.assert_frame_equal(structured_1, structured_2)
    # 行为特征 DataFrame 完全一致
    pd.testing.assert_frame_equal(behavior_1, behavior_2)


def test_feature_provider_different_employees_differ():
    """真实字段值不同的员工生成的特征应不同（真实字段优先）."""
    from app.ml.feature_provider import build_features

    emp1 = _make_employee()
    emp2 = _make_employee()  # 不同 uuid
    emp2.distance_from_home = 12
    s1, _ = build_features(emp1)
    s2, _ = build_features(emp2)

    # 至少有一些结构化特征值不同（反映真实字段差异）
    diff_count = (s1.iloc[0] != s2.iloc[0]).sum()
    assert diff_count > 0, "结构化特征未反映真实字段差异（违反真实字段优先）"


def test_feature_provider_no_random_from_id():
    """P0-4：结构化特征不依赖员工 ID 随机种子（相同真实数据 → 完全相同）."""
    import pandas as pd

    from app.ml.feature_provider import build_structured_features

    emp1 = _make_employee()
    emp2 = _make_employee()  # 不同 uuid、相同真实数据
    pd.testing.assert_frame_equal(
        build_structured_features(emp1), build_structured_features(emp2)
    )


def test_feature_provider_real_field_priority_and_default():
    """P0-4：真实字段优先；缺失字段回退训练分布占位常量."""
    from app.ml.feature_provider import build_structured_features

    # 缺失全部真实字段 → 占位默认值（不随 uuid 变化）
    for _ in range(3):
        assert (build_structured_features(_make_employee()).iloc[0] ==
                build_structured_features(_make_employee()).iloc[0]).all()

    # 显式提供真实值 → 使用真实值（无随机）
    emp = _make_employee()
    emp.distance_from_home = 4
    emp.job_satisfaction = 1
    df = build_structured_features(emp)
    assert int(df.iloc[0]["DistanceFromHome"]) == 4
    assert int(df.iloc[0]["JobSatisfaction"]) == 1


def test_feature_provider_structured_columns_order():
    """结构化特征列顺序应与 STRUCTURED_FEATURE_COLUMNS 一致."""
    from app.ml.feature_provider import build_structured_features

    emp = _make_employee()
    df = build_structured_features(emp)
    assert list(df.columns) == STRUCTURED_FEATURE_COLUMNS
    assert len(df) == 1  # 单行


def test_feature_provider_no_pii_fields():
    """特征中绝不能包含 gender/ethnicity/disability/birth_date."""
    from app.ml.feature_provider import build_structured_features

    emp = _make_employee()
    df = build_structured_features(emp)
    cols = set(df.columns)
    forbidden = {"gender", "ethnicity", "disability", "birth_date"}
    assert not (cols & forbidden), f"特征中包含禁用字段：{cols & forbidden}"


def test_feature_provider_salary_percentile_derived():
    """salary_percentile 应从 employee.salary_percentile 反推（72.50 → 0.725）."""
    from app.ml.feature_provider import build_structured_features

    emp = _make_employee()
    df = build_structured_features(emp)
    assert pytest.approx(df.iloc[0]["salary_percentile"], rel=1e-3) == 0.725
    # MonthlyIncome = salary_percentile * 20000
    assert pytest.approx(df.iloc[0]["MonthlyIncome"], rel=1e-3) == 0.725 * 20000


def test_feature_provider_behavior_columns():
    """行为特征应包含 12 列（3 指标 × 4 统计量）."""
    from app.ml.feature_provider import build_behavior_features

    emp = _make_employee()
    df = build_behavior_features(emp)
    assert len(df.columns) == 12
    assert len(df) == 1
    # 验证列名以统计量后缀结尾
    stats = {"trend_slope", "mean", "std", "recent_change_rate"}
    for col in df.columns:
        assert any(col.endswith(f"_{s}") for s in stats), f"列 {col} 不以统计量结尾"


# ===== 2. pii_crypto 测试 =====


def test_pii_crypto_encrypt_decrypt_roundtrip():
    """encrypt → decrypt 往返一致."""
    plaintext = "张三的敏感信息-ID-12345"
    ciphertext = pii_crypto.encrypt(plaintext)
    assert ciphertext != plaintext  # 密文应不同于明文
    decrypted = pii_crypto.decrypt(ciphertext)
    assert decrypted == plaintext


def test_pii_crypto_encrypt_uniqueness():
    """同一明文两次加密应产生不同密文（Fernet 包含随机 IV）."""
    plaintext = "同一段敏感信息"
    c1 = pii_crypto.encrypt(plaintext)
    c2 = pii_crypto.encrypt(plaintext)
    assert c1 != c2  # Fernet 每次加密包含随机 IV
    # 但都能解密回原文
    assert pii_crypto.decrypt(c1) == plaintext
    assert pii_crypto.decrypt(c2) == plaintext


def test_pii_crypto_hash_field_stable():
    """hash_field 对同一输入应返回相同哈希."""
    plaintext = "13800138000"
    h1 = pii_crypto.hash_field(plaintext)
    h2 = pii_crypto.hash_field(plaintext)
    assert h1 == h2
    # SHA256 应为 64 位十六进制
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_pii_crypto_hash_field_different_inputs():
    """不同输入应产生不同哈希."""
    assert pii_crypto.hash_field("alice") != pii_crypto.hash_field("bob")


def test_pii_crypto_hash_field_not_reversible():
    """哈希应与明文不同（不可逆）."""
    plaintext = "敏感数据"
    h = pii_crypto.hash_field(plaintext)
    assert h != plaintext
    assert plaintext not in h


# ===== 3. RiskService.score_to_level 阈值测试 =====


def test_score_to_level_low():
    """0-19 → low."""
    assert RiskService.score_to_level(0) == "low"
    assert RiskService.score_to_level(19) == "low"


def test_score_to_level_medium_low():
    """20-39 → medium_low."""
    assert RiskService.score_to_level(20) == "medium_low"
    assert RiskService.score_to_level(39) == "medium_low"


def test_score_to_level_medium():
    """40-59 → medium."""
    assert RiskService.score_to_level(40) == "medium"
    assert RiskService.score_to_level(59) == "medium"


def test_score_to_level_medium_high():
    """60-79 → medium_high."""
    assert RiskService.score_to_level(60) == "medium_high"
    assert RiskService.score_to_level(79) == "medium_high"


def test_score_to_level_high():
    """80-100 → high."""
    assert RiskService.score_to_level(80) == "high"
    assert RiskService.score_to_level(100) == "high"


def test_score_to_level_boundary():
    """阈值边界：20/40/60/80 应进位到更高等级."""
    assert RiskService.score_to_level(19) == "low"
    assert RiskService.score_to_level(20) == "medium_low"
    assert RiskService.score_to_level(39) == "medium_low"
    assert RiskService.score_to_level(40) == "medium"
    assert RiskService.score_to_level(59) == "medium"
    assert RiskService.score_to_level(60) == "medium_high"
    assert RiskService.score_to_level(79) == "medium_high"
    assert RiskService.score_to_level(80) == "high"


def test_risk_service_model_version():
    """MODEL_VERSION 应为 fusion-engine-v1（W4 起）."""
    assert RiskService.MODEL_VERSION == "fusion-engine-v1"


# ===== 4. WarningService 申诉转换测试 =====


@dataclass
class _FakeWarning:
    """轻量 WarningRecord 替身."""

    status: str
    level: str
    risk_score: int = 70
    confirmed_at: datetime | None = None
    closed_at: datetime | None = None


def test_appeal_transition_new_confirmed_appealing_legal():
    """new → confirmed → appealing 合法路径."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    # new → confirmed
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    assert w.status == STATUS_CONFIRMED
    # confirmed → appealing（合法）
    from_s, to_s = WarningService.transition(w, STATUS_APPEALING, uuid4())
    assert from_s == STATUS_CONFIRMED
    assert to_s == STATUS_APPEALING
    assert w.status == STATUS_APPEALING


def test_appeal_transition_closed_to_appealing_illegal():
    """closed → appealing 非法（终态不可转换）."""
    w = _FakeWarning(status=STATUS_CLOSED, level=LEVEL_P1)
    with pytest.raises(ValueError, match="终态"):
        WarningService.transition(w, STATUS_APPEALING, uuid4())
    # 状态未变更
    assert w.status == STATUS_CLOSED


def test_appeal_transition_new_to_appealing_illegal():
    """new → appealing 非法（不在允许列表）."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    with pytest.raises(ValueError, match="非法状态转换"):
        WarningService.transition(w, STATUS_APPEALING, uuid4())
    assert w.status == STATUS_NEW


def test_appeal_transition_appealing_to_confirmed_legal():
    """appealing → confirmed 合法（申诉撤回）."""
    w = _FakeWarning(status=STATUS_APPEALING, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    assert w.status == STATUS_CONFIRMED


def test_appeal_transition_appealing_to_closed_legal():
    """appealing → closed 合法（申诉处理后关闭）."""
    w = _FakeWarning(status=STATUS_APPEALING, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CLOSED, uuid4())
    assert w.status == STATUS_CLOSED
    assert w.closed_at is not None


# ===== 5. SHAP 端点 / FusionEngine 集成测试（模型文件存在时） =====

# 模型文件路径
_MODELS_DIR = Path(__file__).resolve().parent.parent / "app" / "ml" / "models"
_STRUCT_MODEL_PATH = _MODELS_DIR / "structured_lgbm.pkl"
_BEHAVIOR_MODEL_PATH = _MODELS_DIR / "behavior_if.pkl"

# 模型文件存在时才运行 SHAP/FusionEngine 测试
_MODELS_AVAILABLE = _STRUCT_MODEL_PATH.exists() and _BEHAVIOR_MODEL_PATH.exists()
_skip_if_no_models = pytest.mark.skipif(
    not _MODELS_AVAILABLE,
    reason="模型文件不存在（structured_lgbm.pkl / behavior_if.pkl），跳过 SHAP/FusionEngine 测试",
)


@_skip_if_no_models
def test_fusion_engine_predict_returns_valid_score():
    """FusionEngine.predict 应返回 0-100 的 risk_score 与合法 level."""
    from app.ml.feature_provider import build_features
    from app.ml.fusion_engine import FusionEngine

    emp = _make_employee()
    structured, behavior = build_features(emp)
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
def test_shap_explainer_returns_top3_factors():
    """ShapExplainer.explain 应返回 Top3 特征贡献."""
    from app.ml.feature_provider import build_structured_features
    from app.ml.shap_explainer import ShapExplainer

    emp = _make_employee()
    structured = build_structured_features(emp)
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
def test_shap_factors_ordered_by_abs_contribution():
    """SHAP factors 应按 |contribution| 降序排列."""
    from app.ml.feature_provider import build_structured_features
    from app.ml.shap_explainer import ShapExplainer

    emp = _make_employee()
    structured = build_structured_features(emp)
    explainer = ShapExplainer()
    factors = explainer.explain(structured, top_k=5)

    abs_contribs = [abs(f["contribution"]) for f in factors]
    assert abs_contribs == sorted(abs_contribs, reverse=True)


@_skip_if_no_models
@pytest.mark.asyncio
async def test_risk_service_predict_with_models():
    """RiskService.predict 集成测试（含模型加载）.

    注意：此测试不传 db（传 None），应抛 ValueError（因为需要查 Employee）。
    验证降级策略：无 db 时抛 ValueError 而非崩溃。
    """
    emp_id = uuid4()
    tenant_id = uuid4()
    # 无 db 会话 → 抛 ValueError（提示需要 db）
    with pytest.raises(ValueError, match="db"):
        await RiskService.predict(emp_id, tenant_id, db=None)


# ===== 6. global_explanation 降级测试 =====


@pytest.mark.asyncio
async def test_global_explanation_no_db_returns_default():
    """global_explanation 无 db 时应返回默认占位."""
    result = await RiskService.global_explanation(uuid4(), window_days=30, db=None)
    assert result["model_version"] == "fusion-engine-v1"
    assert result["window_days"] == 30
    assert len(result["top_features"]) > 0
    assert "computed_at" in result


# ===== 7. WebSocket broadcast 静默跳过测试 =====


@pytest.mark.asyncio
async def test_broadcast_risk_update_no_connections_silent():
    """无连接时 broadcast_risk_update 应静默跳过（不抛异常）."""
    from app.api.v1.ws import broadcast_risk_update

    # 无连接的租户
    await broadcast_risk_update(
        tenant_id="non-existent-tenant",
        message={"type": "risk_update", "employee_id": "x", "risk_score": 75},
    )
    # 不抛异常即通过
