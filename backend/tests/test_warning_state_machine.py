"""WarningService 状态机测试（D04 4.3 + V1.1 修订 + FR-LOOP-004）.

覆盖：
  - P0：confirmed → review 必经，confirmed → fixing 非法
  - P1/P2：confirmed → fixing 可直转
  - 终态 closed 不可再转换
  - 非法转换抛 ValueError
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import pytest

from app.models.warning import (
    LEVEL_P0, LEVEL_P1, LEVEL_P2,
    STATUS_APPEALING, STATUS_CLOSED, STATUS_CONFIRMED,
    STATUS_FIXING, STATUS_NEW, STATUS_REVIEW,
)
from app.services.warning_service import WarningService


@dataclass
class _FakeWarning:
    """轻量 WarningRecord 替身（避免依赖 DB）."""

    status: str
    level: str
    risk_score: int = 80
    confirmed_at: datetime | None = None
    closed_at: datetime | None = None


# ===== 基础合法转换 =====


def test_new_to_confirmed_legal():
    """new → confirmed 合法（任意 level）."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P0)
    from_s, to_s = WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    assert from_s == STATUS_NEW
    assert to_s == STATUS_CONFIRMED
    assert w.status == STATUS_CONFIRMED
    assert w.confirmed_at is not None


def test_new_to_closed_legal():
    """new → closed 合法（直接关闭）."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CLOSED, uuid4())
    assert w.status == STATUS_CLOSED
    assert w.closed_at is not None


# ===== P0 强制路径：confirmed → review → fixing =====


def test_p0_confirmed_to_fixing_illegal():
    """P0 confirmed → fixing 非法（必须经 review，FR-LOOP-004）."""
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P0)
    with pytest.raises(ValueError, match="P0"):
        WarningService.transition(w, STATUS_FIXING, uuid4())
    # 状态未变更
    assert w.status == STATUS_CONFIRMED


def test_p0_confirmed_to_review_legal():
    """P0 confirmed → review 合法."""
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P0)
    from_s, to_s = WarningService.transition(w, STATUS_REVIEW, uuid4())
    assert to_s == STATUS_REVIEW
    assert w.status == STATUS_REVIEW


def test_p0_full_path_confirmed_review_fixing():
    """P0 完整路径：confirmed → review → fixing → closed."""
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P0)

    # confirmed → review
    WarningService.transition(w, STATUS_REVIEW, uuid4())
    assert w.status == STATUS_REVIEW

    # review → fixing（HR 经理复核通过后进入干预）
    WarningService.transition(w, STATUS_FIXING, uuid4())
    assert w.status == STATUS_FIXING

    # fixing → closed
    WarningService.transition(w, STATUS_CLOSED, uuid4())
    assert w.status == STATUS_CLOSED
    assert w.closed_at is not None


# ===== P1/P2 直转 fixing =====


def test_p1_confirmed_to_fixing_legal():
    """P1 confirmed → fixing 可直转（无需复核）."""
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P1)
    from_s, to_s = WarningService.transition(w, STATUS_FIXING, uuid4())
    assert to_s == STATUS_FIXING
    assert w.status == STATUS_FIXING


def test_p2_confirmed_to_fixing_legal():
    """P2 confirmed → fixing 可直转."""
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P2)
    WarningService.transition(w, STATUS_FIXING, uuid4())
    assert w.status == STATUS_FIXING


# ===== 终态与非法转换 =====


def test_closed_terminal_cannot_transition():
    """closed 为终态，不可再转换."""
    w = _FakeWarning(status=STATUS_CLOSED, level=LEVEL_P0)
    with pytest.raises(ValueError, match="终态"):
        WarningService.transition(w, STATUS_CONFIRMED, uuid4())


def test_fixing_to_new_illegal():
    """fixing → new 非法（不在允许列表）."""
    w = _FakeWarning(status=STATUS_FIXING, level=LEVEL_P1)
    with pytest.raises(ValueError, match="非法状态转换"):
        WarningService.transition(w, STATUS_NEW, uuid4())


def test_appealing_to_fixing_illegal():
    """appealing → fixing 非法."""
    w = _FakeWarning(status=STATUS_APPEALING, level=LEVEL_P1)
    with pytest.raises(ValueError, match="非法状态转换"):
        WarningService.transition(w, STATUS_FIXING, uuid4())


def test_appealing_to_confirmed_legal():
    """appealing → confirmed 合法（申诉撤回）."""
    w = _FakeWarning(status=STATUS_APPEALING, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    assert w.status == STATUS_CONFIRMED


# ===== allowed_next_statuses 按 level 过滤 =====


def test_allowed_next_p0_confirmed_excludes_fixing():
    """P0 confirmed 的可转换列表中应排除 fixing."""
    allowed = WarningService.allowed_next_statuses(STATUS_CONFIRMED, LEVEL_P0)
    assert STATUS_FIXING not in allowed
    assert STATUS_REVIEW in allowed


def test_allowed_next_p1_confirmed_includes_fixing():
    """P1 confirmed 的可转换列表中应包含 fixing."""
    allowed = WarningService.allowed_next_statuses(STATUS_CONFIRMED, LEVEL_P1)
    assert STATUS_FIXING in allowed
    assert STATUS_REVIEW in allowed


# ===== 等级判定 =====


def test_level_from_score_p0():
    """risk_score >= 80 → P0."""
    assert WarningService.level_from_score(80) == LEVEL_P0
    assert WarningService.level_from_score(95) == LEVEL_P0


def test_level_from_score_p1():
    """60 <= risk_score < 80 → P1."""
    assert WarningService.level_from_score(60) == LEVEL_P1
    assert WarningService.level_from_score(79) == LEVEL_P1


def test_level_from_score_p2_trend():
    """risk_score 上升 >= 20 → P2（趋势预警）."""
    assert WarningService.level_from_score(50, prev_score=30) == LEVEL_P2
    # 上升不足 20 仍归 P1
    assert WarningService.level_from_score(45, prev_score=30) == LEVEL_P1
