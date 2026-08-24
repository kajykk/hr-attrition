"""行为事件基建测试（README 路线图第一步）.

覆盖：
  - 迁移 0005：revision 链、建表契约列、复合索引（occurred_at DESC）、downgrade 回滚
  - BehaviorEvent ORM 模型与迁移契约一致
  - behavior_service.record_behavior_event 写入读出 + 失败容忍（批量）
  - 登录事件接线：email 匹配到员工记 login 事件；未匹配跳过
  - 预警流转事件接线：payload 含 from/to 状态
  - feature_provider 行为模态真实路径（real）与回退路径（demo）双分支
"""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pandas as pd
import pytest

from app.models.behavior_event import BehaviorEvent
from app.services import behavior_service
from app.services.behavior_service import (
    EVENT_LOGIN,
    EVENT_WARNING_TRANSITION,
    record_behavior_event,
    record_behavior_events,
    record_login_event_for_user,
    record_warning_transition_event,
)

_MIGRATION_FILE = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0005_behavior_events.py"

# ============================================================
# 1. 迁移 0005 契约测试
# ============================================================


def test_migration_0005_revision_chain():
    """0005 必须接在 0004 之后（revision/down_revision 契约）."""
    src = _MIGRATION_FILE.read_text(encoding="utf-8")
    assert 'revision = "0005"' in src
    assert 'down_revision = "0004"' in src


def test_migration_0005_upgrade_creates_contract_columns():
    """upgrade 应创建 behavior_events 表且包含全部契约列."""
    src = _MIGRATION_FILE.read_text(encoding="utf-8")
    upgrade_block = src.split("def upgrade")[1].split("def downgrade")[0]
    assert '"behavior_events"' in upgrade_block
    # 契约列全集
    for col in ("id", "tenant_id", "employee_id", "event_type", "occurred_at", "payload"):
        assert f'"{col}"' in upgrade_block, f"迁移缺少契约列 {col}"
    # 类型契约：varchar(64) / timestamptz / jsonb 默认 {}
    assert "sa.String(64)" in upgrade_block
    assert "sa.DateTime(timezone=True)" in upgrade_block
    assert "JSONB()" in upgrade_block
    assert "'{}'::jsonb" in upgrade_block


def test_migration_0005_upgrade_id_server_default_gen_random_uuid():
    """id 主键应有 DB 侧 gen_random_uuid() 默认值."""
    src = _MIGRATION_FILE.read_text(encoding="utf-8")
    assert "gen_random_uuid()" in src
    assert "primary_key=True" in src


def test_migration_0005_creates_composite_index_desc():
    """应创建复合索引 ix_behavior_events_tenant_emp_time 且 occurred_at 为 DESC."""
    src = _MIGRATION_FILE.read_text(encoding="utf-8")
    upgrade_block = src.split("def upgrade")[1].split("def downgrade")[0]
    assert "ix_behavior_events_tenant_emp_time" in upgrade_block
    assert 'sa.text("occurred_at DESC")' in upgrade_block
    # 列顺序：tenant_id → employee_id → occurred_at
    idx_pos = upgrade_block.find('"tenant_id", "employee_id"')
    assert idx_pos > 0, "索引列顺序应为 tenant_id, employee_id, occurred_at DESC"


def test_migration_0005_downgrade_drops_table_and_index():
    """downgrade 应回滚索引与表."""
    src = _MIGRATION_FILE.read_text(encoding="utf-8")
    drop_block = src.split("def downgrade")[1]
    assert "drop_index" in drop_block
    assert "ix_behavior_events_tenant_emp_time" in drop_block
    assert "drop_table" in drop_block
    assert '"behavior_events"' in drop_block


def test_behavior_event_model_matches_migration_contract():
    """ORM 模型列集合与迁移契约一致（无 DB，仅读 mapper 元数据）."""
    model_cols = set(BehaviorEvent.__mapper__.columns.keys())
    expected = {"id", "tenant_id", "employee_id", "event_type", "occurred_at", "payload"}
    assert model_cols == expected, f"模型列与迁移契约不一致: 差异={model_cols ^ expected}"
    # 复合索引名一致
    index_names = {idx.name for idx in BehaviorEvent.__table__.indexes}
    assert "ix_behavior_events_tenant_emp_time" in index_names


# ============================================================
# 2. behavior_service 写入读出测试（mock DB 会话，随现有惯例）
# ============================================================


def _make_db():
    """构造 mock 异步会话（add/flush 可断言）."""
    db = AsyncMock()
    db.add = MagicMock()
    db.add_all = MagicMock()  # add_all 为同步方法，用 MagicMock 避免未 await 告警
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_record_behavior_event_write_read():
    """record_behavior_event 应构造正确字段并 flush（写入读出）."""
    db = _make_db()
    tenant_id, employee_id = uuid4(), uuid4()

    event = await record_behavior_event(
        db=db,
        tenant_id=tenant_id,
        employee_id=employee_id,
        event_type=EVENT_LOGIN,
        payload={"source": "web"},
    )

    assert event is not None
    assert event.tenant_id == tenant_id
    assert event.employee_id == employee_id
    assert event.event_type == EVENT_LOGIN
    assert event.payload == {"source": "web"}
    assert event.occurred_at is not None
    db.add.assert_called_once_with(event)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_behavior_event_defaults_payload_to_empty_dict():
    """payload 缺省应为 {} 而非 None."""
    db = _make_db()
    event = await record_behavior_event(db=db, tenant_id=uuid4(), employee_id=uuid4(),
                                        event_type="login")
    assert event.payload == {}


@pytest.mark.asyncio
async def test_record_behavior_event_failure_tolerant_returns_none():
    """写入失败应吞异常返回 None（best-effort，不阻断业务）."""
    db = _make_db()
    db.flush = AsyncMock(side_effect=RuntimeError("db down"))

    event = await record_behavior_event(
        db=db, tenant_id=uuid4(), employee_id=uuid4(), event_type="login",
    )
    assert event is None


@pytest.mark.asyncio
async def test_record_behavior_events_batch_writes_all():
    """批量写入应 add_all 全部事件并返回条数."""
    db = _make_db()
    tenant_id = uuid4()
    n = await record_behavior_events(
        db=db,
        tenant_id=tenant_id,
        events=[
            {"employee_id": uuid4(), "event_type": "login"},
            {"employee_id": uuid4(), "event_type": "email", "payload": {"n": 3}},
        ],
    )
    assert n == 2
    db.add_all.assert_called_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_behavior_events_empty_list_is_noop():
    """空批量应直接返回 0（不触碰 DB）."""
    db = _make_db()
    assert await record_behavior_events(db=db, tenant_id=uuid4(), events=[]) == 0
    db.add_all.assert_not_called()


@pytest.mark.asyncio
async def test_record_login_event_records_login_when_employee_matched(monkeypatch):
    """登录事件：email 匹配到员工时应记 login 事件."""
    db = _make_db()
    tenant_id, employee_id = uuid4(), uuid4()

    async def _fake_resolve(session, tid, email):
        assert tid == tenant_id
        return employee_id

    monkeypatch.setattr(behavior_service, "resolve_employee_id_by_email", _fake_resolve)

    user = MagicMock()
    user.tenant_id = tenant_id
    user.email = "emp@corp.com"
    user.id = uuid4()
    user.role = "employee"

    await record_login_event_for_user(db, user)

    db.add.assert_called_once()
    event = db.add.call_args.args[0]
    assert isinstance(event, BehaviorEvent)
    assert event.event_type == EVENT_LOGIN
    assert event.employee_id == employee_id
    assert event.payload["user_id"] == str(user.id)


@pytest.mark.asyncio
async def test_record_login_event_skips_without_employee_match(monkeypatch):
    """未匹配到员工（如 HR 管理账号）应跳过，不写任何事件."""

    async def _fake_resolve(session, tid, email):
        return None

    monkeypatch.setattr(behavior_service, "resolve_employee_id_by_email", _fake_resolve)

    db = _make_db()
    user = MagicMock()
    user.tenant_id = uuid4()
    user.email = "admin@corp.com"
    user.id = uuid4()

    await record_login_event_for_user(db, user)
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_record_warning_transition_event_payload_contains_statuses():
    """预警流转事件的 payload 应含 warning_id/level/from/to/operator."""
    db = _make_db()
    warning = MagicMock()
    warning.tenant_id = uuid4()
    warning.employee_id = uuid4()
    warning.id = uuid4()
    warning.level = "P1"
    operator_id = uuid4()

    await record_warning_transition_event(db, warning, operator_id, "confirmed", "review")

    event = db.add.call_args.args[0]
    assert event.event_type == EVENT_WARNING_TRANSITION
    assert event.employee_id == warning.employee_id
    assert event.payload["from_status"] == "confirmed"
    assert event.payload["to_status"] == "review"
    assert event.payload["operator_id"] == str(operator_id)


# ============================================================
# 3. feature_provider 行为模态 real/demo 双路径
# ============================================================


def _make_employee_mock():
    emp = MagicMock()
    emp.id = uuid4()
    return emp


def _events_rows(n_days: int = 6, per_day: int = 2):
    """构造近 n_days 天的 天×类型 计数行（date_trunc 后的 day 为 datetime）."""
    now = datetime.now(UTC)
    rows = []
    for d in range(n_days):
        day = now - timedelta(days=d)
        day_trunc = datetime(day.year, day.month, day.day, tzinfo=UTC)
        rows.append((day_trunc, "login", per_day))
        rows.append((day_trunc, "email", per_day))
        rows.append((day_trunc, "meeting", per_day))
        rows.append((day_trunc, "meeting_decline", 1))
    return rows


@pytest.mark.asyncio
async def test_provider_real_path_with_sufficient_events():
    """近 30 天事件充足时应走真实路径：source='real'，12 列契约对齐."""
    from app.ml.feature_provider import build_behavior_features_from_events

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.all.return_value = _events_rows()
    db.execute = AsyncMock(return_value=result_mock)

    df, source = await build_behavior_features_from_events(db, uuid4(), _make_employee_mock())

    assert source == "real"
    assert len(df.columns) == 12  # 3 指标 × 4 统计量
    assert len(df) == 1
    stats = {"trend_slope", "mean", "std", "recent_change_rate"}
    for col in df.columns:
        assert any(col.endswith(f"_{s}") for s in stats)
    # meeting_decline_rate 特征应落在 [0, 1] 邻域（比率量纲）
    rate_cols = [c for c in df.columns if c.startswith("meeting_decline_rate")]
    for c in rate_cols:
        if c.endswith("_mean"):
            assert 0.0 <= float(df.iloc[0][c]) <= 1.0


@pytest.mark.asyncio
async def test_provider_real_path_deterministic_within_same_day():
    """同一批数据两次聚合应产出完全相同的特征（确定性）."""
    from app.ml.feature_provider import build_behavior_features_from_events

    rows = _events_rows()

    def _make_db_with(rows_data):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = rows_data
        db.execute = AsyncMock(return_value=result_mock)
        return db

    df1, s1 = await build_behavior_features_from_events(_make_db_with(rows), uuid4(), _make_employee_mock())
    df2, s2 = await build_behavior_features_from_events(_make_db_with(rows), uuid4(), _make_employee_mock())
    assert s1 == s2 == "real"
    pd.testing.assert_frame_equal(df1, df2)


@pytest.mark.asyncio
async def test_provider_demo_fallback_without_events():
    """近 30 天无事件时应回退 demo（source='demo'）."""
    from app.ml.feature_provider import (
        BEHAVIOR_DATA_SOURCE_DEMO,
        build_behavior_features,
        build_behavior_features_from_events,
    )

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)

    emp = _make_employee_mock()
    df, source = await build_behavior_features_from_events(db, uuid4(), emp)

    assert source == BEHAVIOR_DATA_SOURCE_DEMO
    # 回退结果应与 demo 构造完全一致（复用同一路径）
    pd.testing.assert_frame_equal(df, build_behavior_features(emp))


@pytest.mark.asyncio
async def test_provider_demo_fallback_below_threshold():
    """事件总数低于阈值（<5）时也应回退 demo."""
    from app.ml.feature_provider import build_behavior_features_from_events

    db = AsyncMock()
    result_mock = MagicMock()
    now = datetime.now(UTC)
    day_trunc = datetime(now.year, now.month, now.day, tzinfo=UTC)
    result_mock.all.return_value = [
        (day_trunc, "login", 2),
    ]
    db.execute = AsyncMock(return_value=result_mock)

    _, source = await build_behavior_features_from_events(db, uuid4(), _make_employee_mock())
    assert source == "demo"


@pytest.mark.asyncio
async def test_provider_demo_fallback_on_query_error():
    """聚合查询异常（如表未迁移）应降级 demo 且不抛异常."""
    from app.ml.feature_provider import build_behavior_features_from_events

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("relation behavior_events does not exist"))

    df, source = await build_behavior_features_from_events(db, uuid4(), _make_employee_mock())

    assert source == "demo"
    assert len(df.columns) == 12


@pytest.mark.asyncio
async def test_provider_real_path_ignores_outside_window_and_unknown_types():
    """窗口外事件与未知 event_type 应被忽略（不计入总数）."""
    from app.ml.feature_provider import (
        BEHAVIOR_EVENTS_MIN_THRESHOLD,
        build_behavior_features_from_events,
    )

    now = datetime.now(UTC)
    old_day = now - timedelta(days=45)
    old_trunc = datetime(old_day.year, old_day.month, old_day.day, tzinfo=UTC)
    recent_trunc = datetime(now.year, now.month, now.day, tzinfo=UTC)

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.all.return_value = [
        (old_trunc, "login", 100),          # 窗口外 → 忽略
        (recent_trunc, "unknown_type", 99),  # 未知类型 → 忽略
        (recent_trunc, "login", 2),          # 窗口内但总数 < 阈值 → demo
    ]
    db.execute = AsyncMock(return_value=result_mock)

    _, source = await build_behavior_features_from_events(db, uuid4(), _make_employee_mock())
    # 有效事件仅 2 条 < BEHAVIOR_EVENTS_MIN_THRESHOLD
    assert source == "demo"
    assert BEHAVIOR_EVENTS_MIN_THRESHOLD >= 1  # 契约自检：阈值为正


def test_provider_real_and_demo_share_column_contract():
    """real 与 demo 两路径的输出列集合必须一致（维度对齐现有契约）."""
    from app.ml.feature_provider import BEHAVIOR_SERIES, build_behavior_features

    demo_df = build_behavior_features(_make_employee_mock())
    assert len(demo_df.columns) == 12
    # 列顺序契约：BEHAVIOR_SERIES 顺序 × 每指标 4 统计量（与共享统计 helper 一致）
    expected = [
        f"{prefix}_{stat}"
        for prefix in BEHAVIOR_SERIES
        for stat in ("trend_slope", "mean", "std", "recent_change_rate")
    ]
    assert list(demo_df.columns) == expected


def test_migration_file_registered_in_models_package():
    """BehaviorEvent 应注册进 app.models（Alembic autogenerate 依赖）."""
    import app.models as models_pkg

    assert hasattr(models_pkg, "BehaviorEvent")
    assert "BehaviorEvent" in models_pkg.__all__
