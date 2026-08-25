"""WarningService 状态机测试（D04 4.3 + V1.1 修订 + FR-LOOP-004）.

覆盖：
  - P0：confirmed → review 必经，confirmed → fixing 非法
  - P1/P2：confirmed → fixing 可直转
  - 终态 closed 不可再转换
  - 非法转换抛 ValueError
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.warning import (
    LEVEL_P0,
    LEVEL_P1,
    LEVEL_P2,
    STATUS_APPEALING,
    STATUS_CLOSED,
    STATUS_CONFIRMED,
    STATUS_FIXING,
    STATUS_NEW,
    STATUS_REVIEW,
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
    _, to_s = WarningService.transition(w, STATUS_REVIEW, uuid4())
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
    _, to_s = WarningService.transition(w, STATUS_FIXING, uuid4())
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


# ============================================================
# 边界补测：分数临界 / 兜底语义 / 幂等防重 / 申诉回退 / 时间戳 / 租户隔离
# ============================================================


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (59.9, LEVEL_P1),   # 浮点低于 int 阈值 60：不进位，走默认 P1
        (60, LEVEL_P1),     # P1 下界（含）
        (79.9, LEVEL_P1),   # 浮点 79.9 不四舍五入到 P0 阈值
        (80, LEVEL_P0),     # P0 下界（含）
        (89.9, LEVEL_P0),
        (90, LEVEL_P0),
    ],
)
def test_level_from_score_threshold_boundaries(score, expected):
    """分数临界：60/80 为硬阈值，浮点输入按数值比较、不进位."""
    assert WarningService.level_from_score(score) == expected


def test_level_from_score_p2_trend_exact_20_rise():
    """趋势 P2 恰好上升 20 触发；19 不触发；分数下降不触发；高分优先于趋势."""
    assert WarningService.level_from_score(55, prev_score=35) == LEVEL_P2  # +20 恰达阈值
    assert WarningService.level_from_score(54, prev_score=35) == LEVEL_P1  # +19
    assert WarningService.level_from_score(30, prev_score=80) == LEVEL_P1  # 下降
    assert WarningService.level_from_score(95, prev_score=95) == LEVEL_P0  # 高分判定优先
    assert WarningService.level_from_score(95, prev_score=70) == LEVEL_P0  # 同时满足 P0 与 P2 时取 P0


def test_level_from_score_no_prev_fallback_semantics():
    """无 prev_score 兜底：<60 且无趋势信号时默认 P1（不产生更低等级），显式 None 等价省略."""
    assert WarningService.level_from_score(50) == LEVEL_P1
    assert WarningService.level_from_score(50, prev_score=None) == LEVEL_P1
    # 无 prev 即便当前分低也不判 P2（无法计算趋势）；0 分与异常负分同样兜底 P1
    assert WarningService.level_from_score(10) == LEVEL_P1
    assert WarningService.level_from_score(0) == LEVEL_P1
    assert WarningService.level_from_score(-5) == LEVEL_P1


def test_level_from_score_int_domain_sweep_matches_docstring():
    """DB 列 risk_score 为 Integer：全 int 值域扫描无异常，且 >=80 必为 P0、<80 不为 P0."""
    for s in range(-10, 105):
        level = WarningService.level_from_score(s)
        assert level in (LEVEL_P0, LEVEL_P1, LEVEL_P2)
        if s >= 80:
            assert level == LEVEL_P0
        else:
            assert level != LEVEL_P0


def test_level_from_score_deterministic_same_input_same_output():
    """同分重复判定确定性（纯函数）：相同输入重复调用结果一致，无建警副作用可依赖."""
    for args in [(65,), (85,), (55, 30), (59,)]:
        first = WarningService.level_from_score(*args)
        second = WarningService.level_from_score(*args)
        assert first == second


def test_repeated_same_target_transition_rejected_state_dedup():
    """重复提交同一目标转换被状态机天然去重：第二次 confirmed→confirmed 抛 ValueError."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    first_ts = w.confirmed_at

    with pytest.raises(ValueError, match="非法状态转换"):
        WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    # 状态与确认时间戳未被重复操作破坏
    assert w.status == STATUS_CONFIRMED
    assert w.confirmed_at == first_ts


def test_double_confirm_replay_only_first_wins():
    """并发重放语义（顺序模拟两个操作员同请求）：仅首次生效，重放方收到 ValueError.

    注：实现无显式锁/乐观锁，幂等完全依赖转换前状态校验（见报告疑点）。
    """
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P2)
    op1, op2 = uuid4(), uuid4()
    from_s, to_s = WarningService.transition(w, STATUS_CONFIRMED, op1)
    assert (from_s, to_s) == (STATUS_NEW, STATUS_CONFIRMED)

    with pytest.raises(ValueError):
        WarningService.transition(w, STATUS_CONFIRMED, op2)
    assert w.status == STATUS_CONFIRMED
    assert w.confirmed_at is not None


@pytest.mark.parametrize(
    "target",
    [
        STATUS_NEW,
        STATUS_CONFIRMED,
        STATUS_REVIEW,
        STATUS_FIXING,
        STATUS_APPEALING,
        STATUS_CLOSED,  # 自环亦拒绝
    ],
)
def test_closed_terminal_full_target_matrix(target):
    """终态封闭性矩阵：closed → 任意目标（含自环）全部非法且状态保持."""
    w = _FakeWarning(status=STATUS_CLOSED, level=LEVEL_P1)
    with pytest.raises(ValueError, match="终态"):
        WarningService.transition(w, target, uuid4())
    assert w.status == STATUS_CLOSED
    assert WarningService.is_terminal(w.status)


def test_transition_never_mutates_level_or_risk_score():
    """服务层不存在自动升降级：完整生命周期中 level/risk_score 恒定（升级只由建警时分级决定）."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    w.risk_score = 65
    path = [STATUS_CONFIRMED, STATUS_REVIEW, STATUS_FIXING, STATUS_CLOSED]
    for target in path:
        WarningService.transition(w, target, uuid4())
        assert w.level == LEVEL_P1, f"转换到 {target} 后 level 被意外改动"
        assert w.risk_score == 65, f"转换到 {target} 后 risk_score 被意外改动"


def test_confirmed_at_preserved_through_intermediate_transitions():
    """confirmed_at 只在进入 confirmed 时写入，中间流转（review/fixing）不得触碰."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    sentinel = datetime(2020, 1, 1, tzinfo=UTC)
    w.confirmed_at = sentinel  # 哨兵旧值，检测中途是否被改写

    WarningService.transition(w, STATUS_REVIEW, uuid4())
    assert w.confirmed_at == sentinel
    WarningService.transition(w, STATUS_FIXING, uuid4())
    assert w.confirmed_at == sentinel


def test_appeal_rejection_overwrites_confirmed_at_with_new_time():
    """申诉驳回回退（appealing→confirmed）会以当前时间覆盖 confirmed_at.

    实测行为：首次确认时间丢失（见报告疑点），此处固化现状防止无感变更。
    """
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    old_confirm = datetime(2020, 1, 1, tzinfo=UTC)
    w.confirmed_at = old_confirm

    WarningService.transition(w, STATUS_APPEALING, uuid4())
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())  # HR 驳回申诉
    assert w.status == STATUS_CONFIRMED
    assert w.confirmed_at > old_confirm


def test_new_to_closed_sets_closed_at_without_confirming():
    """new→closed 直关路径：写 closed_at，confirmed_at 保持 None（从未确认过）."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P2)
    WarningService.transition(w, STATUS_CLOSED, uuid4())
    assert w.status == STATUS_CLOSED
    assert w.closed_at is not None
    assert w.confirmed_at is None


def test_p0_after_appeal_roundtrip_still_requires_review():
    """P0 申诉驳回回到 confirmed 后，FR-LOOP-004 复核限制不丢失：仍禁直转 fixing."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P0)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    WarningService.transition(w, STATUS_APPEALING, uuid4())
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())  # 驳回回退

    with pytest.raises(ValueError, match="FR-LOOP-004"):
        WarningService.transition(w, STATUS_FIXING, uuid4())
    assert w.status == STATUS_CONFIRMED
    # 经 review 后仍可正常干预
    WarningService.transition(w, STATUS_REVIEW, uuid4())
    WarningService.transition(w, STATUS_FIXING, uuid4())
    assert w.status == STATUS_FIXING


def test_appeal_refile_after_rejection_allowed_no_counter():
    """申诉驳回后可再次发起（confirmed→appealing 循环合法，无次数上限——疑点见报告）."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())

    for _ in range(3):  # 反复申诉-驳回均合法
        WarningService.transition(w, STATUS_APPEALING, uuid4())
        assert w.status == STATUS_APPEALING
        WarningService.transition(w, STATUS_CONFIRMED, uuid4())
        assert w.status == STATUS_CONFIRMED


def test_fixing_to_appealing_illegal():
    """干预执行中（fixing）不可发起申诉，只能 review 或 closed."""
    w = _FakeWarning(status=STATUS_FIXING, level=LEVEL_P1)
    with pytest.raises(ValueError, match="非法状态转换"):
        WarningService.transition(w, STATUS_APPEALING, uuid4())
    assert w.status == STATUS_FIXING


def test_review_fixing_ping_pong_loop_legal():
    """review↔fixing 可反复横跳（复核打回再干预无循环上限——疑点见报告）."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P0)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    WarningService.transition(w, STATUS_REVIEW, uuid4())

    for _ in range(3):
        WarningService.transition(w, STATUS_FIXING, uuid4())
        assert w.status == STATUS_FIXING
        WarningService.transition(w, STATUS_REVIEW, uuid4())
        assert w.status == STATUS_REVIEW


# ===== 租户隔离（API 层：mock DB + dependency_overrides，仓库既有惯例） =====


def _api_env(role="hr_manager", found=True):
    """构造 API 测试环境：HR 用户 + JWT + 捕获 SQL 的 mock db（返回 overrides 所需全部对象）.

    found=False 模拟跨租户/不存在：所有查询按租户过滤后查空。
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.api import deps
    from app.core.security import create_access_token
    from app.db.session import get_db
    from app.main import app

    tenant_a = uuid4()
    hr_user = SimpleNamespace(id=uuid4(), tenant_id=tenant_a, role=role, status="active")
    token = create_access_token(str(hr_user.id), str(tenant_a), role)

    row = SimpleNamespace(
        id=uuid4(),
        employee_id=uuid4(),
        prediction_id=None,
        level=LEVEL_P1,
        risk_score=70,
        status=STATUS_CONFIRMED,
        assigned_to=None,
        escalated_to=None,
        message="边界测试预警",
        created_at=datetime.now(UTC),
        confirmed_at=None,
        closed_at=None,
        tenant_id=tenant_a,
    )

    calls: list = []
    db = AsyncMock()
    db.add = MagicMock()  # add 为同步方法，避免 AsyncMock 产生未 await 协程告警

    async def _capture_execute(stmt):
        calls.append(stmt)
        result = MagicMock()
        # 首查即按 mock 返回；租户过滤正确性由断言检查 SQL 本身
        result.scalar_one_or_none.return_value = row if found else None
        result.scalar_one.return_value = 1 if found else 0
        result.scalars.return_value.all.return_value = [row] if found else []
        return result

    db.execute = AsyncMock(side_effect=_capture_execute)

    async def _fake_get_current_user():
        return hr_user

    async def _fake_get_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_get_current_user
    app.dependency_overrides[get_db] = _fake_get_db

    return {"app": app, "token": token, "user": hr_user, "tenant": tenant_a,
            "db": db, "calls": calls, "row": row}


def _stmt_tenant_params(stmt) -> set[str]:
    """编译语句并提取绑定参数值字符串（用于断言 tenant 过滤存在且指向正确租户）."""
    return {str(v) for k, v in stmt.compile().params.items() if "tenant_id" in k}


def test_api_status_update_scoped_to_jwt_tenant_and_auth_operator(client):
    """PATCH /status：查询必须带 JWT 租户过滤；操作人取认证用户而非客户端入参."""
    env = _api_env()
    wid = env["row"].id
    try:
        resp = client.patch(
            f"/api/v1/warnings/{wid}/status",
            json={"target_status": STATUS_CLOSED},
            headers={"Authorization": f"Bearer {env['token']}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == STATUS_CLOSED

        # 首条查询语句必须携带 tenant_id 过滤，且值等于 JWT 租户 A
        assert env["calls"], "应至少执行一次查询"
        tenant_values = _stmt_tenant_params(env["calls"][0])
        assert str(env["tenant"]) in tenant_values

        # 事件操作人来自认证用户（非 payload.operator_id）
        from app.models.warning import WarningEvent

        added_events = [c.args[0] for c in env["db"].add.call_args_list
                        if isinstance(c.args[0], WarningEvent)]
        assert len(added_events) == 1
        assert added_events[0].operator_id == env["user"].id
        assert added_events[0].from_status == STATUS_CONFIRMED
        assert added_events[0].to_status == STATUS_CLOSED
    finally:
        env["app"].dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path_tpl", "payload"),
    [
        ("GET", "/api/v1/warnings/{wid}", None),
        ("PATCH", "/api/v1/warnings/{wid}/status", {"target_status": STATUS_CLOSED}),
        ("POST", "/api/v1/warnings/{wid}/appeal", {"reason": "误报"}),
        ("POST", "/api/v1/warnings/{wid}/mark", {"mark_type": "false_positive"}),
    ],
)
def test_api_cross_tenant_lookup_returns_404_not_leaked(client, method, path_tpl, payload):
    """租户外资源一律 404（不泄露存在性），且查询语句带本租户过滤."""
    env = _api_env(found=False)  # 模拟跨租户：按租户过滤后查不到
    try:
        resp = client.request(
            method,
            path_tpl.format(wid=uuid4()),  # 用随机 ID：命中与否只取决于 mock 返回
            json=payload,
            headers={"Authorization": f"Bearer {env['token']}"},
        )
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]
        # 服务端确实按租户过滤查询（而非未加过滤的全表命中）
        assert str(env["tenant"]) in _stmt_tenant_params(env["calls"][0])
    finally:
        env["app"].dependency_overrides.clear()


def test_api_list_warnings_count_and_page_both_tenant_scoped(client):
    """GET /warnings：count 与分页两条语句都必须带 JWT 租户过滤."""
    env = _api_env()
    try:
        resp = client.get(
            "/api/v1/warnings",
            headers={"Authorization": f"Bearer {env['token']}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["level"] == LEVEL_P1

        assert len(env["calls"]) == 2  # count + 分页
        for stmt in env["calls"]:
            assert str(env["tenant"]) in _stmt_tenant_params(stmt)
    finally:
        env["app"].dependency_overrides.clear()
