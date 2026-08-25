"""WarningService 状态机测试（D04 4.3 + V1.1 修订 + FR-LOOP-004 + 五项补齐）.

覆盖：
  - P0：confirmed → review 必经，confirmed → fixing 非法
  - P1/P2：confirmed → fixing 可直转
  - 终态 closed 不可再转换
  - 非法转换抛 ValueError
五项补齐（feat/rag-kb）：
  1. 防重复建警：create_warning 去重 / 合并升级 / 冲突 retry（并发模拟）
  3. 并发锁：apply_transition FOR UPDATE 行锁 + retry 一次
  4. 申诉次数上限：check_appeal_limit + API 409
  5. 保留首次确认时间：appealing→confirmed 不覆盖 confirmed_at
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.warning import (
    LEVEL_P0,
    LEVEL_P1,
    LEVEL_P2,
    MAX_APPEAL_COUNT,
    STATUS_APPEALING,
    STATUS_CLOSED,
    STATUS_CONFIRMED,
    STATUS_FIXING,
    STATUS_NEW,
    STATUS_REVIEW,
    WarningEvent,
    WarningRecord,
)
from app.services.warning_service import (
    SYSTEM_OPERATOR_ID,
    AppealLimitExceeded,
    WarningService,
)


@dataclass
class _FakeWarning:
    """轻量 WarningRecord 替身（避免依赖 DB）."""

    status: str
    level: str
    risk_score: int = 80
    confirmed_at: datetime | None = None
    closed_at: datetime | None = None
    appeal_count: int = 0


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


def test_appeal_rejection_preserves_first_confirmed_at():
    """申诉驳回回退（appealing→confirmed）不再覆盖 confirmed_at（保留首次确认时间）.

    V1.2 修订（状态机五项补齐 5）：仅当 confirmed_at 为空时才写入，
    驳回回退后仍保留员工首次被确认的时间戳。
    """
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    old_confirm = datetime(2020, 1, 1, tzinfo=UTC)
    w.confirmed_at = old_confirm

    WarningService.transition(w, STATUS_APPEALING, uuid4())
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())  # HR 驳回申诉
    assert w.status == STATUS_CONFIRMED
    assert w.confirmed_at == old_confirm  # 首次确认时间未被覆盖


def test_confirmed_at_written_only_when_empty():
    """confirmed_at 仅在为空时写入：已有时再次进入 confirmed 不改写."""
    w = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    sentinel = datetime(2020, 1, 1, tzinfo=UTC)
    w.confirmed_at = sentinel

    # 模拟申诉驳回回退路径：appealing → confirmed，且已有历史确认时间
    w.status = STATUS_APPEALING
    WarningService.transition(w, STATUS_CONFIRMED, uuid4())
    assert w.confirmed_at == sentinel

    # 对照：为空时正常写入
    w2 = _FakeWarning(status=STATUS_NEW, level=LEVEL_P1)
    assert w2.confirmed_at is None
    WarningService.transition(w2, STATUS_CONFIRMED, uuid4())
    assert w2.confirmed_at is not None
    assert w2.confirmed_at != sentinel


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


def test_appeal_refile_allowed_below_limit_blocked_at_cap():
    """申诉驳回后可再次发起（confirmed→appealing 循环合法），但 appeal_count 达上限后拒绝.

    V1.2 修订（状态机五项补齐 4）：appealing 入口 >= MAX_APPEAL_COUNT 次抛
    AppealLimitExceeded（API 层映射 409「申诉次数已达上限」）。
    """
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P1, appeal_count=0)

    for _ in range(3):  # 上限内反复申诉-驳回均合法
        WarningService.check_appeal_limit(w)
        WarningService.transition(w, STATUS_APPEALING, uuid4())
        assert w.status == STATUS_APPEALING
        WarningService.transition(w, STATUS_CONFIRMED, uuid4())
        assert w.status == STATUS_CONFIRMED
        w.appeal_count += 1

    # 达上限后再发起 → 拒绝
    with pytest.raises(AppealLimitExceeded, match="申诉次数已达上限"):
        WarningService.check_appeal_limit(w)


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


def _api_env(role="hr_manager", found=True, appeal_count=0):
    """构造 API 测试环境：HR 用户 + JWT + 捕获 SQL 的 mock db（返回 overrides 所需全部对象）.

    found=False 模拟跨租户/不存在：所有查询按租户过滤后查空。
    appeal_count 模拟预警已申诉次数（409 上限测试用）。
    """
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
        appeal_count=appeal_count,
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


# ============================================================
# 五项补齐 1：防重复建警（create_warning 去重 / 合并升级 / 冲突 retry）
# ============================================================


class _FakeResult:
    """mock execute 结果：scalars().all() 与 scalar_one_or_none 双通道."""

    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._scalar


def _make_service_db(results=None, flush_side_effects=None):
    """服务层 mock 会话：execute 按队列弹出结果；flush 可注入异常模拟并发冲突."""
    db = MagicMock()
    db.executed_stmts = []
    queue = list(results or [])

    async def _execute(stmt):
        db.executed_stmts.append(stmt)
        return queue.pop(0)

    db.execute = _execute
    db.add = MagicMock()
    if flush_side_effects is not None:
        effects = list(flush_side_effects)
        effects.extend([None] * 10)  # 列表耗尽后放行（避免 StopIteration 噪声）
        db.flush = AsyncMock(side_effect=effects)
    else:
        db.flush = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _make_record(level=LEVEL_P1, status=STATUS_NEW, tenant=None, employee=None, **kwargs):
    """构造真实 ORM WarningRecord（不触库，显式赋 id 供断言）."""
    defaults = dict(
        id=uuid4(),
        tenant_id=tenant or uuid4(),
        employee_id=employee or uuid4(),
        level=level,
        risk_score=70,
        status=status,
        appeal_count=0,
        created_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return WarningRecord(**defaults)


def _added_objects_of(db, cls):
    """从 mock db.add 调用中筛出指定类型的对象."""
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], cls)]


@pytest.mark.asyncio
async def test_create_warning_new_employee_creates_record_and_event():
    """无未关闭预警 → 新建 new 状态记录 + created 事件，created=True."""
    db = _make_service_db(results=[_FakeResult(rows=[])])
    tenant_id, employee_id = uuid4(), uuid4()

    w, created = await WarningService.create_warning(
        db=db, tenant_id=tenant_id, employee_id=employee_id,
        level=LEVEL_P1, risk_score=72,
    )

    assert created is True
    assert w.status == STATUS_NEW
    assert w.level == LEVEL_P1
    assert w.risk_score == 72
    assert w.tenant_id == tenant_id and w.employee_id == employee_id
    events = _added_objects_of(db, WarningEvent)
    assert len(events) == 1
    assert events[0].action == "created"
    assert events[0].operator_id == SYSTEM_OPERATOR_ID


@pytest.mark.asyncio
async def test_create_warning_same_level_active_skips():
    """同员工已有未关闭同级预警 → 跳过建警（返回现有记录，不新增）."""
    existing = _make_record(level=LEVEL_P1, status=STATUS_CONFIRMED)
    db = _make_service_db(results=[_FakeResult(rows=[existing])])

    w, created = await WarningService.create_warning(
        db=db, tenant_id=existing.tenant_id, employee_id=existing.employee_id,
        level=LEVEL_P1, risk_score=75,
    )

    assert created is False
    assert w.id == existing.id
    assert w.risk_score == 70  # 未被改写
    db.add.assert_not_called()  # 无新预警/事件写入


@pytest.mark.asyncio
async def test_create_warning_higher_level_merges_into_existing():
    """已有 P1 在办 + 新预测 P0 → 升级合并到现有记录（不新建），并写 escalated 事件."""
    existing = _make_record(level=LEVEL_P1, risk_score=65)
    db = _make_service_db(results=[_FakeResult(rows=[existing])])

    w, created = await WarningService.create_warning(
        db=db, tenant_id=existing.tenant_id, employee_id=existing.employee_id,
        level=LEVEL_P0, risk_score=88,
    )

    assert created is False
    assert w.id == existing.id
    assert w.level == LEVEL_P0          # 合并升级
    assert w.risk_score == 88           # 取更高风险分
    events = _added_objects_of(db, WarningEvent)
    assert len(events) == 1
    assert events[0].action == "escalated"
    assert "P1" in events[0].comment and "P0" in events[0].comment


@pytest.mark.asyncio
async def test_create_warning_lower_level_absorbed_by_higher_active():
    """已有 P0 在办 + 新预测 P2 → 不降级合并，直接吸收跳过."""
    existing = _make_record(level=LEVEL_P0, risk_score=90)
    db = _make_service_db(results=[_FakeResult(rows=[existing])])

    w, created = await WarningService.create_warning(
        db=db, tenant_id=existing.tenant_id, employee_id=existing.employee_id,
        level=LEVEL_P2, risk_score=50,
    )

    assert created is False
    assert w.id == existing.id
    assert w.level == LEVEL_P0  # 保持最高级别
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_warning_unique_conflict_retry_once_dedups():
    """并发模拟：flush 撞部分唯一索引 → rollback 后 retry 重查命中同级在办 → 跳过.

    模拟两管线同时为同一员工建警：attempt1 查空后 INSERT 冲突；
    attempt2（另一事务已提交）重查即见同级 active → 走去重分支。
    """
    concurrent_active = _make_record(level=LEVEL_P1, status=STATUS_NEW)
    db = _make_service_db(
        results=[
            _FakeResult(rows=[]),                    # attempt1：查无 active
            _FakeResult(rows=[concurrent_active]),   # attempt2：冲突对手已提交
        ],
        flush_side_effects=[IntegrityError("INSERT", {}, Exception("uq_warnings_active"))],
    )

    w, created = await WarningService.create_warning(
        db=db, tenant_id=uuid4(), employee_id=uuid4(),
        level=LEVEL_P1, risk_score=66,
    )

    assert created is False
    assert w.id == concurrent_active.id
    assert db.rollback.await_count == 1  # 仅 retry 一次
    assert db.flush.await_count == 1     # 第二次走跳过分支不再 flush


# ============================================================
# 五项补齐 3：并发锁（apply_transition 统一入口 + FOR UPDATE + retry）
# ============================================================


def _locked_row(status=STATUS_NEW, level=LEVEL_P1):
    return SimpleNamespace(
        status=status, level=level,
        confirmed_at=None, closed_at=None, appeal_count=0,
    )


@pytest.mark.asyncio
async def test_load_for_update_statement_carries_for_update_clause():
    """load_for_update 生成的 SELECT 必须带 FOR UPDATE 行锁子句."""
    captured = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeResult(scalar=None)

    db = MagicMock()
    db.execute = _execute
    result = await WarningService.load_for_update(db, uuid4(), uuid4())

    assert result is None
    stmt = captured["stmt"]
    assert getattr(stmt, "_for_update_arg", None) is not None, "查询必须 with_for_update"


@pytest.mark.asyncio
async def test_load_for_update_filters_by_tenant_binding():
    """行锁查询必须携带租户过滤（防跨租户加锁/读取）."""
    tenant_id = uuid4()
    captured = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeResult(scalar=None)

    db = MagicMock()
    db.execute = _execute
    await WarningService.load_for_update(db, tenant_id, uuid4())

    assert str(tenant_id) in _stmt_tenant_params(captured["stmt"])


@pytest.mark.asyncio
async def test_apply_transition_happy_path_returns_from_to_and_flushes():
    """统一入口正常路径：行锁加载 → 转换 → flush，返回 (warning, from, to)."""
    row = _locked_row()
    db = _make_service_db(results=[_FakeResult(scalar=row)])

    res = await WarningService.apply_transition(
        db=db, tenant_id=uuid4(), warning_id=uuid4(),
        target_status=STATUS_CONFIRMED, operator_id=uuid4(),
    )

    assert res is not None
    w, from_s, to_s = res
    assert w is row
    assert (from_s, to_s) == (STATUS_NEW, STATUS_CONFIRMED)
    assert w.confirmed_at is not None
    assert getattr(db.executed_stmts[0], "_for_update_arg", None) is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_transition_missing_returns_none_not_found_semantics():
    """预警不存在/跨租户 → 返回 None（API 层映射 404），不写任何数据."""
    db = _make_service_db(results=[_FakeResult(rows=[])])
    res = await WarningService.apply_transition(
        db=db, tenant_id=uuid4(), warning_id=uuid4(),
        target_status=STATUS_CLOSED, operator_id=uuid4(),
    )
    assert res is None
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_transition_integrity_error_retries_once_then_succeeds():
    """并发模拟：flush 唯一冲突 → rollback → 重新加锁加载 → 转换成功（仅 retry 一次）.

    场景：操作员确认预警的同时，另一事务释放/占用了该员工同级唯一槽位，
    flush 撞唯一索引 → 回退重新加载后成功。
    """
    stale_row = _locked_row(status=STATUS_NEW)
    fresh_row = _locked_row(status=STATUS_NEW)
    db = _make_service_db(
        results=[_FakeResult(scalar=stale_row), _FakeResult(scalar=fresh_row)],
        flush_side_effects=[IntegrityError("UPDATE", {}, Exception("uq_warnings_active"))],
    )

    res = await WarningService.apply_transition(
        db=db, tenant_id=uuid4(), warning_id=uuid4(),
        target_status=STATUS_CONFIRMED, operator_id=uuid4(),
    )

    assert res is not None
    w, from_s, to_s = res
    assert w is fresh_row  # retry 后基于新快照转换
    assert (from_s, to_s) == (STATUS_NEW, STATUS_CONFIRMED)
    assert db.rollback.await_count == 1
    assert len(db.executed_stmts) == 2   # 两次行锁加载
    assert all(getattr(s, "_for_update_arg", None) is not None for s in db.executed_stmts)


# ============================================================
# 五项补齐 4：申诉次数上限（check_appeal_limit + API 409）
# ============================================================


def test_check_appeal_limit_allows_below_cap():
    """appeal_count < 上限时校验放行（不抛异常）."""
    for count in range(MAX_APPEAL_COUNT):
        w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P1, appeal_count=count)
        WarningService.check_appeal_limit(w)  # 不抛即通过


def test_check_appeal_limit_rejects_at_cap_with_message():
    """appeal_count >= 3 时拒绝，错误信息含「申诉次数已达上限」（API 映射 409）."""
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P1, appeal_count=MAX_APPEAL_COUNT)
    with pytest.raises(AppealLimitExceeded, match="申诉次数已达上限"):
        WarningService.check_appeal_limit(w)
    # AppealLimitExceeded 是 ValueError 子类：上层 except ValueError 兜底仍成立
    assert issubclass(AppealLimitExceeded, ValueError)


@pytest.mark.parametrize("count", [MAX_APPEAL_COUNT, MAX_APPEAL_COUNT + 5])
def test_check_appeal_limit_boundary_counts(count):
    """恰好达上限与超限均拒绝（边界语义：>= 而非 >）."""
    w = _FakeWarning(status=STATUS_CONFIRMED, level=LEVEL_P1, appeal_count=count)
    with pytest.raises(AppealLimitExceeded):
        WarningService.check_appeal_limit(w)


def test_api_status_update_goes_through_row_lock(client):
    """PATCH /status 的转换必须经 apply_transition（查询含 FOR UPDATE 行锁子句）."""
    env = _api_env()
    try:
        resp = client.patch(
            f"/api/v1/warnings/{env['row'].id}/status",
            json={"target_status": STATUS_CLOSED},
            headers={"Authorization": f"Bearer {env['token']}"},
        )
        assert resp.status_code == 200
        locked = [s for s in env["calls"] if getattr(s, "_for_update_arg", None) is not None]
        assert locked, "状态转换入口应使用 SELECT ... FOR UPDATE"
    finally:
        env["app"].dependency_overrides.clear()


def test_api_appeal_rejected_409_when_limit_reached(client):
    """POST /appeal：appeal_count >= 3 → 409「申诉次数已达上限」，且不发生转换."""
    env = _api_env(appeal_count=MAX_APPEAL_COUNT)
    try:
        resp = client.post(
            f"/api/v1/warnings/{env['row'].id}/appeal",
            json={"reason": "误报"},
            headers={"Authorization": f"Bearer {env['token']}"},
        )
        assert resp.status_code == 409
        assert "申诉次数已达上限" in resp.json()["detail"]
        assert env["row"].status == STATUS_CONFIRMED      # 状态未被改变
        assert env["row"].appeal_count == MAX_APPEAL_COUNT  # 计数未自增
    finally:
        env["app"].dependency_overrides.clear()


def test_api_appeal_success_increments_appeal_count(client):
    """POST /appeal 成功路径：转入 appealing 且 appeal_count 自增."""
    env = _api_env()  # row.status=confirmed，appeal_count=0
    try:
        resp = client.post(
            f"/api/v1/warnings/{env['row'].id}/appeal",
            json={"reason": "数据过期"},
            headers={"Authorization": f"Bearer {env['token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == STATUS_APPEALING
        assert env["row"].appeal_count == 1
    finally:
        env["app"].dependency_overrides.clear()


# ============================================================
# 五项补齐 2：自动升级（plan_escalation 纯函数 + beat 接线 + sweep 集成）
# ============================================================


def _stale_warning(level=LEVEL_P2, hours=25.0, tenant=None, escalated_to=None):
    return _make_record(
        level=level,
        status=STATUS_NEW,
        tenant=tenant or uuid4(),
        created_at=datetime.now(UTC) - timedelta(hours=hours),
        escalated_to=escalated_to,
    )


def test_plan_escalation_matrix_24h_48h_and_caps():
    """升级矩阵：<24h 不动；24-48h 升一级封顶 P0；>=48h 追加 escalated_to 标记."""
    from app.tasks.warning_escalation import (
        FINAL_ESCALATE_AFTER,
        STALE_ESCALATE_AFTER,
        plan_escalation,
    )

    now = datetime.now(UTC)
    # 新鲜预警不动（< 24h）
    fresh = _stale_warning(hours=(STALE_ESCALATE_AFTER.total_seconds() - 3600) / 3600)
    assert plan_escalation(fresh, now) is None

    # 24h+ 升一级
    mid = _stale_warning(level=LEVEL_P2, hours=25)
    assert plan_escalation(mid, now) == {"new_level": LEVEL_P1, "need_manager": False}

    # 封顶 P0 后无动作
    capped = _stale_warning(level=LEVEL_P0, hours=30)
    assert plan_escalation(capped, now) is None

    # 48h+：P1 → P0 且需 manager 标记
    old = _stale_warning(level=LEVEL_P1,
                         hours=FINAL_ESCALATE_AFTER.total_seconds() / 3600 + 1)
    assert plan_escalation(old, now) == {"new_level": LEVEL_P0, "need_manager": True}

    # P0 已封顶但超 48h：仍需 manager 标记（由调用方幂等去重 escalated_to）
    marked = _stale_warning(level=LEVEL_P0,
                            hours=FINAL_ESCALATE_AFTER.total_seconds() / 3600 + 1)
    plan = plan_escalation(marked, now)
    assert plan["new_level"] is None and plan["need_manager"] is True


class _FakeEscalationSession:
    """run_escalation_sweep 专用假会话：execute 队列 + add/commit 捕获."""

    def __init__(self, results):
        self._queue = list(results)
        self.added: list = []
        self.committed = False
        self.executed_stmts: list = []

    async def execute(self, stmt):
        self.executed_stmts.append(stmt)
        return self._queue.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def test_escalation_task_registered_and_scheduled_every_6h():
    """Celery 任务已注册，beat 调度含 warning-escalate-stale 且周期为每 6 小时."""
    from celery.schedules import crontab

    from app.celery_app import celery_app

    assert "app.tasks.warning_escalation.escalate_stale_warnings" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule
    entry = schedule.get("warning-escalate-stale")
    assert entry is not None, "beat 缺少 warning-escalate-stale 条目"
    assert entry["task"] == "app.tasks.warning_escalation.escalate_stale_warnings"
    cron = entry["schedule"]
    assert isinstance(cron, crontab)
    assert cron._orig_hour == "*/6"
    assert str(cron._orig_minute) == "0"


@pytest.mark.asyncio
async def test_run_escalation_sweep_upgrades_level_and_marks_hr_manager(monkeypatch):
    """sweep 集成：25h P2 → 升 P1；50h P1 → 升 P0 且 escalated_to 指向 HR 经理."""
    import app.tasks.warning_escalation as esc_mod

    now = datetime.now(UTC)
    tenant_id = uuid4()
    manager_id = uuid4()
    w_a = _stale_warning(level=LEVEL_P2, hours=25, tenant=tenant_id)   # 仅升一级
    w_b = _stale_warning(level=LEVEL_P1, hours=50, tenant=tenant_id)   # 升级 + 终态升级

    session = _FakeEscalationSession(results=[
        _FakeResult(rows=[w_a, w_b]),          # 过期未确认预警集（FOR UPDATE）
        _FakeResult(scalar=manager_id),        # 租户内 HR 经理查询
    ])
    monkeypatch.setattr(esc_mod, "async_session_factory", lambda: session)

    stats = await esc_mod.run_escalation_sweep(now=now)

    assert stats["status"] == "ok"
    assert stats["checked"] == 2
    assert stats["level_upgraded"] == 2      # w_a: P2→P1；w_b: P1→P0
    assert stats["final_escalated"] == 1     # w_b 写 escalated_to
    assert w_a.level == LEVEL_P1 and w_a.escalated_to is None
    assert w_b.level == LEVEL_P0 and w_b.escalated_to == manager_id
    events = [x for x in session.added if isinstance(x, WarningEvent)]
    assert len(events) == 3                  # w_b 同时产生升级 + 终态标记两条事件
    assert all(e.action == "escalated" for e in events)
    final_marks = [e for e in events if "HR 经理" in (e.comment or "")]
    assert len(final_marks) == 1
    assert getattr(session.executed_stmts[0], "_for_update_arg", None) is not None
    assert session.committed is True


@pytest.mark.asyncio
async def test_run_escalation_sweep_skips_fresh_warnings(monkeypatch):
    """未过期（<24h）预警不被扫描命中时无任何动作、不写事件."""
    import app.tasks.warning_escalation as esc_mod

    now = datetime.now(UTC)
    fresh = _stale_warning(level=LEVEL_P2, hours=2)
    session = _FakeEscalationSession(results=[_FakeResult(rows=[fresh])])
    monkeypatch.setattr(esc_mod, "async_session_factory", lambda: session)

    stats = await esc_mod.run_escalation_sweep(now=now)

    assert stats["checked"] == 1
    assert stats["level_upgraded"] == 0 and stats["final_escalated"] == 0
    assert session.added == []
    assert fresh.level == LEVEL_P2
    assert session.committed is True


@pytest.mark.asyncio
async def test_run_escalation_sweep_no_manager_found_skips_final_mark(monkeypatch):
    """租户内无 HR 经理：48h 预警仅升一级，escalated_to 保持空且不写终态事件."""
    import app.tasks.warning_escalation as esc_mod

    now = datetime.now(UTC)
    stale = _stale_warning(level=LEVEL_P1, hours=50)
    session = _FakeEscalationSession(results=[
        _FakeResult(rows=[stale]),
        _FakeResult(scalar=None),  # 无可用 HR 经理
    ])
    monkeypatch.setattr(esc_mod, "async_session_factory", lambda: session)

    stats = await esc_mod.run_escalation_sweep(now=now)

    assert stats["level_upgraded"] == 1
    assert stats["final_escalated"] == 0
    assert stale.level == LEVEL_P0
    assert stale.escalated_to is None


# ============================================================
# 迁移 0006 契约：appeal_count 列 + 部分唯一索引（防重复建警 DB 兜底）
# ============================================================

_MIGRATION_0006_FILE = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0006_warning_dedup_appeal_limit.py"
)


def test_migration_0006_revision_chain():
    """0006 必须接在 0005 之后."""
    src = _MIGRATION_0006_FILE.read_text(encoding="utf-8")
    assert 'revision = "0006"' in src
    assert 'down_revision = "0005"' in src


def test_migration_0006_adds_appeal_count_and_unique_index():
    """upgrade 应新增 appeal_count 列与部分唯一索引；downgrade 全部回滚."""
    from app.models.warning import UQ_ACTIVE_WARNINGS_INDEX

    src = _MIGRATION_0006_FILE.read_text(encoding="utf-8")
    upgrade_block = src.split("def upgrade")[1].split("def downgrade")[0]
    downgrade_block = src.split("def downgrade")[1]

    # appeal_count 列契约：Integer + server_default 0
    assert '"appeal_count"' in upgrade_block
    assert "sa.Integer()" in upgrade_block
    assert 'sa.text("0")' in upgrade_block
    # 部分唯一索引契约（索引名经常量引用，取值即 uq_warnings_active_tenant_emp_level）
    assert UQ_ACTIVE_WARNINGS_INDEX == "uq_warnings_active_tenant_emp_level"
    assert "UQ_ACTIVE_WARNINGS_INDEX" in upgrade_block
    assert '"tenant_id", "employee_id", "level"' in upgrade_block
    assert "postgresql_where" in upgrade_block
    for status in ("new", "confirmed", "review", "fixing", "appealing"):
        assert f"'{status}'" in upgrade_block, f"唯一索引过滤缺少状态 {status}"
    assert "unique=True" in upgrade_block

    # downgrade 覆盖回滚
    assert "drop_index" in downgrade_block
    assert "UQ_ACTIVE_WARNINGS_INDEX" in downgrade_block
    assert "drop_column" in downgrade_block
    assert '"appeal_count"' in downgrade_block


def test_warning_model_matches_migration_0006_contract():
    """ORM 模型应含 appeal_count 列与同名部分唯一索引（无 DB，仅读元数据）."""
    mapper_cols = set(WarningRecord.__mapper__.columns.keys())
    assert "appeal_count" in mapper_cols
    index_names = {idx.name for idx in WarningRecord.__table__.indexes}
    assert "uq_warnings_active_tenant_emp_level" in index_names
    idx = next(i for i in WarningRecord.__table__.indexes
               if i.name == "uq_warnings_active_tenant_emp_level")
    assert idx.unique is True
    cols = [c.name for c in idx.columns]
    assert cols == ["tenant_id", "employee_id", "level"]


def test_active_statuses_constant_covers_all_non_terminal_states():
    """ACTIVE_STATUSES 应恰好等于全部非终态（防重复建警作用域契约）."""
    from app.models.warning import ACTIVE_STATUSES

    all_statuses = {STATUS_NEW, STATUS_CONFIRMED, STATUS_REVIEW,
                    STATUS_FIXING, STATUS_APPEALING, STATUS_CLOSED}
    assert set(ACTIVE_STATUSES) == all_statuses - {STATUS_CLOSED}
