"""覆盖率补测 - risk_service / warning_service 关键分支（10-15 个带断言单测）.

risk_service：
  - _aggregate_feature_contributions：空行/单值跳过/方向判定/非数值剔除/Top-10 截断
  - get_feature_display_name 未知透传
  - predict 返回 behavior_data_source（行为模态来源标注，README 路线图第一步）
warning_service：
  - transition 时间戳维护（confirmed_at / closed_at）
  - is_terminal 仅 closed 为终态
  - level_from_score 边界（80/60、趋势 +20、默认 P1）
  - allowed_next_statuses 未知状态 / validate_transition 报错文案
"""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.warning import (
    LEVEL_P0,
    LEVEL_P1,
    LEVEL_P2,
    STATUS_CLOSED,
    STATUS_CONFIRMED,
    STATUS_FIXING,
    STATUS_NEW,
    STATUS_REVIEW,
)
from app.services.risk_service import (
    RiskService,
    _aggregate_feature_contributions,
    get_feature_display_name,
)
from app.services.warning_service import WarningService

# ============================================================
# 1. risk_service._aggregate_feature_contributions 分支
# ============================================================

_DEFAULT_TOP = [
    {"feature": "salary_percentile", "display_name": "薪资分位",
     "contribution": -0.18, "direction": "negative"},
]


def test_aggregate_contributions_empty_rows_returns_default():
    """无 feature_values 行时应返回默认占位 Top 特征."""
    result = _aggregate_feature_contributions([], _DEFAULT_TOP)
    assert result == _DEFAULT_TOP


def test_aggregate_contributions_skips_single_value_features():
    """样本数 < 2 的特征应被跳过（无法计算方差贡献）."""
    rows = [{"Age": 30}, {"Age": 40, "Salary": 100}]  # Salary 仅 1 个样本
    result = _aggregate_feature_contributions(rows, _DEFAULT_TOP)
    feats = [c["feature"] for c in result]
    assert "Age" in feats
    assert "Salary" not in feats


def test_aggregate_contributions_direction_positive_when_mean_above_median():
    """均值高于中位数 → direction=positive."""
    # 均值 (0+1+2)/3 = 1 > 中位数 1？构造明显偏斜：[1, 1, 1, 9] 均值 3 > 中位数 1
    rows = [{"X": v} for v in (1.0, 1.0, 1.0, 9.0)]
    result = _aggregate_feature_contributions(rows, _DEFAULT_TOP)
    assert result[0]["feature"] == "X"
    assert result[0]["direction"] == "positive"


def test_aggregate_contributions_direction_negative_when_mean_below_median():
    """均值低于中位数 → direction=negative."""
    rows = [{"Y": v} for v in (-9.0, -1.0, -1.0, -1.0)]  # 均值 -3 < 中位数 -1
    result = _aggregate_feature_contributions(rows, _DEFAULT_TOP)
    assert result[0]["feature"] == "Y"
    assert result[0]["direction"] == "negative"


def test_aggregate_contributions_skips_non_numeric_values():
    """非数值取值应被剔除，不产生 NaN 贡献."""
    rows = [
        {"A": "not-a-number", "B": 1.0},
        {"A": None, "B": 3.0},
        {"B": 5.0},
    ]
    result = _aggregate_feature_contributions(rows, _DEFAULT_TOP)
    feats = [c["feature"] for c in result]
    assert "A" not in feats
    assert "B" in feats


def test_aggregate_contributions_caps_at_top10_sorted_by_abs():
    """特征多于 10 个时应按 |contribution| 降序截断为 10."""
    # 每个特征 3 个递增样本；离散度随 i 增大 → 贡献度可区分排序
    rows = []
    for i in range(15):
        base = float(i)
        rows.append({f"F{i:02d}": base})
        rows.append({f"F{i:02d}": base + 2.0 * (i + 1)})
        rows.append({f"F{i:02d}": base + 4.0 * (i + 1)})

    result = _aggregate_feature_contributions(rows, _DEFAULT_TOP)
    assert len(result) == 10
    abs_contribs = [abs(c["contribution"]) for c in result]
    assert abs_contribs == sorted(abs_contribs, reverse=True)
    # 离散度最大的 F14 应排第一
    assert result[0]["feature"] == "F14"


def test_get_feature_display_name_unknown_passthrough():
    """未知特征名应原样返回（不抛错、不返回 None）."""
    assert get_feature_display_name("totally_unknown_feature") == "totally_unknown_feature"
    assert get_feature_display_name("MonthlyIncome") == "月薪"


# ============================================================
# 2. risk_service.predict behavior_data_source 标注
# ============================================================


@pytest.mark.asyncio
async def test_predict_annotates_behavior_data_source_demo(monkeypatch):
    """无真实行为事件时 predict 应返回 behavior_data_source='demo'."""
    from app.services import risk_service

    risk_service._reset_singletons()
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: None)
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: None)
    monkeypatch.setattr(risk_service, "get_redis", lambda: None)

    async def _fake_broadcast(**kwargs):
        pass

    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    emp_id, tenant_id = uuid4(), uuid4()
    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"

    # 第一次 execute 返回员工；后续（行为事件聚合 .all()）走 MagicMock 空迭代 → demo 回退
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    fake_record = MagicMock()
    fake_record.id = uuid4()
    monkeypatch.setattr("app.services.risk_service.RiskPrediction", lambda **kwargs: fake_record)
    result = await RiskService.predict(emp_id, tenant_id, db=db)

    assert result["behavior_data_source"] == "demo"


# ============================================================
# 3. warning_service 关键分支
# ============================================================


class _W:
    """轻量 WarningRecord 替身."""

    def __init__(self, status, level=LEVEL_P1):
        self.status = status
        self.level = level
        self.confirmed_at = None
        self.closed_at = None


def test_transition_sets_confirmed_at_on_confirm():
    """转换到 confirmed 应写入 confirmed_at 时间戳."""
    w = _W(STATUS_NEW)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    assert w.confirmed_at is not None
    assert w.closed_at is None


def test_transition_sets_closed_at_on_close():
    """转换到 closed 应写入 closed_at 时间戳."""
    w = _W(STATUS_REVIEW)
    WarningService.transition(w, STATUS_CLOSED, uuid4())
    assert w.closed_at is not None


def test_is_terminal_only_for_closed():
    """is_terminal 应仅对 closed 返回 True."""
    assert WarningService.is_terminal(STATUS_CLOSED) is True
    for s in (STATUS_NEW, STATUS_CONFIRMED, STATUS_REVIEW, STATUS_FIXING, "appealing"):
        assert WarningService.is_terminal(s) is False


def test_level_from_score_boundaries_p0_p1():
    """等级边界：>=80 为 P0，60-79 为 P1，59 即使上升 20 以下也判 P1/P2 规则."""
    assert WarningService.level_from_score(80) == LEVEL_P0
    assert WarningService.level_from_score(79) == LEVEL_P1
    assert WarningService.level_from_score(60) == LEVEL_P1
    assert WarningService.level_from_score(59, prev_score=40) == LEVEL_P1  # 升 19 < 20


def test_level_from_score_trend_p2_requires_rise_ge_20():
    """P2 趋势预警：风险分上升 >= 20 才触发（升 19 不触发）."""
    assert WarningService.level_from_score(55, prev_score=35) == LEVEL_P2
    assert WarningService.level_from_score(55, prev_score=36) == LEVEL_P1


def test_level_from_score_default_p1_without_prev():
    """无 prev_score 且分数 <60 时默认 P1（最低预警等级）."""
    assert WarningService.level_from_score(50) == LEVEL_P1
    assert WarningService.level_from_score(0) == LEVEL_P1


def test_allowed_next_statuses_unknown_status_returns_empty():
    """未知当前状态应返回空列表（不抛错）."""
    assert WarningService.allowed_next_statuses("nonexistent_status", LEVEL_P1) == []


def test_allowed_next_statuses_review_includes_fixing_and_closed():
    """review 可转 fixing/closed（复核后退回整改或关闭）."""
    nxt = WarningService.allowed_next_statuses(STATUS_REVIEW, LEVEL_P1)
    assert set(nxt) == {STATUS_CLOSED, STATUS_FIXING}


def test_validate_transition_error_mentions_allowed_targets():
    """非法转换报错文案应包含合法目标列表（便于上层 422 提示）."""
    with pytest.raises(ValueError) as exc_info:
        WarningService.validate_transition(STATUS_NEW, "fixing", LEVEL_P1)
    msg = str(exc_info.value)
    assert "new" in msg and "fixing" in msg and "合法目标" in msg


def test_validate_transition_closed_error_mentions_terminal():
    """closed 终态转换报错应提示终态语义."""
    with pytest.raises(ValueError, match="终态"):
        WarningService.validate_transition(STATUS_CLOSED, STATUS_CONFIRMED, LEVEL_P0)


def test_p0_confirmed_to_fixing_forbidden_even_via_allowed_list():
    """P0 confirmed→fixing 虽在基础合法列表中，仍必须被 level 条件拒绝."""
    w = _W(STATUS_CONFIRMED, level=LEVEL_P0)
    with pytest.raises(ValueError, match="复核"):
        WarningService.transition(w, STATUS_FIXING, uuid4())
    assert w.status == STATUS_CONFIRMED  # 状态未被篡改


def test_transition_returns_from_to_tuple_for_event_recording():
    """transition 应回传 (from_status, to_status) 供事件/审计记录使用."""
    w = _W(STATUS_CONFIRMED, level=LEVEL_P0)
    from_s, to_s = WarningService.transition(w, STATUS_REVIEW, uuid4())
    assert from_s == STATUS_CONFIRMED
    assert to_s == STATUS_REVIEW
