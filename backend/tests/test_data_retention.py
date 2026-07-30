"""W8 数据保留任务测试 - C-COMP-06 物理删除 + C-COMP-05 保留策略.

覆盖：
  - purge_departed_employees：DB 不可用降级 / 无候选 / 正常删除 / 单条失败不阻塞
  - report_retention_status：DB 不可用降级 / 正常统计
  - _list_purge_candidates：未结申诉员工被排除
  - _purge_employee_data：删除顺序正确，audit_log 写入
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.tasks import data_retention
from app.tasks.data_retention import (
    RETENTION_YEARS_AFTER_LEAVE,
    RETENTION_YEARS_WARNINGS,
    _list_purge_candidates,
    _purge_employee_data,
    purge_departed_employees,
    report_retention_status,
)


# ============================================================
# 辅助：构造 Employee ORM 对象替身
# ============================================================


class _FakeEmployee:
    """Employee ORM 替身."""

    def __init__(self, leave_date: date | None, status: str = "left") -> None:
        self.id = uuid4()
        self.tenant_id = uuid4()
        self.status = status
        self.leave_date = leave_date


# ============================================================
# 1. purge_departed_employees 入口测试
# ============================================================


def test_purge_invalid_tenant_id_returns_skipped():
    """非法 tenant_id 应直接返回 skipped."""
    result = purge_departed_employees(tenant_id="not-a-uuid")
    assert result["status"] == "skipped"
    assert "tenant_id 非法" in result["reason"]


def test_purge_db_unavailable_returns_skipped():
    """候选查询 DB 异常应返回 skipped 而非抛出."""
    with patch.object(
        data_retention, "_list_purge_candidates", new=AsyncMock(side_effect=RuntimeError("connection refused"))
    ):
        result = purge_departed_employees()
    assert result["status"] == "skipped"
    assert "DB 不可用" in result["reason"]


def test_purge_no_candidates_returns_ok_zero():
    """无候选员工应返回 ok + purged_count=0."""
    with patch.object(data_retention, "_list_purge_candidates", new=AsyncMock(return_value=[])):
        result = purge_departed_employees()
    assert result["status"] == "ok"
    assert result["purged_count"] == 0
    assert result["details"] == []


def test_purge_all_success_returns_ok_with_details():
    """全部成功删除应返回 ok + details 列表."""
    emp1 = _FakeEmployee(leave_date=date.today() - timedelta(days=800))
    emp2 = _FakeEmployee(leave_date=date.today() - timedelta(days=900))
    detail1 = {"employee_id": str(emp1.id), "warning_events": 0, "warnings": 0, "risk_predictions": 3, "employee": 1}
    detail2 = {"employee_id": str(emp2.id), "warning_events": 2, "warnings": 1, "risk_predictions": 5, "employee": 1}

    with patch.object(data_retention, "_list_purge_candidates", new=AsyncMock(return_value=[emp1, emp2])), \
         patch.object(data_retention, "_purge_employee_data", new=AsyncMock(side_effect=[detail1, detail2])):
        result = purge_departed_employees()

    assert result["status"] == "ok"
    assert result["purged_count"] == 2
    assert result["failed_count"] == 0
    assert len(result["details"]) == 2
    assert result["retention_years"] == RETENTION_YEARS_AFTER_LEAVE


def test_purge_partial_failure_returns_partial():
    """部分失败应返回 partial + failures 列表，其他员工仍被删除."""
    emp_ok = _FakeEmployee(leave_date=date.today() - timedelta(days=800))
    emp_fail = _FakeEmployee(leave_date=date.today() - timedelta(days=900))
    detail_ok = {"employee_id": str(emp_ok.id), "warning_events": 0, "warnings": 0, "risk_predictions": 0, "employee": 1}

    with patch.object(data_retention, "_list_purge_candidates", new=AsyncMock(return_value=[emp_ok, emp_fail])), \
         patch.object(
             data_retention,
             "_purge_employee_data",
             new=AsyncMock(side_effect=[detail_ok, RuntimeError("fk constraint")]),
         ):
        result = purge_departed_employees()

    assert result["status"] == "partial"
    assert result["purged_count"] == 1
    assert result["failed_count"] == 1
    assert result["failures"][0]["error"] == "fk constraint"


# ============================================================
# 2. report_retention_status 测试
# ============================================================


def test_report_retention_status_db_unavailable_returns_skipped():
    """DB 异常应返回 skipped."""
    with patch("app.tasks.data_retention.asyncio.run", side_effect=RuntimeError("db down")):
        result = report_retention_status()
    assert result["status"] == "skipped"
    assert "DB 不可用" in result["reason"]


def test_report_retention_status_returns_stats():
    """正常应返回统计字段."""
    in_retention_result = MagicMock()
    in_retention_result.scalar.return_value = 12
    pending_purge_result = MagicMock()
    pending_purge_result.scalar.return_value = 3
    unclosed_result = MagicMock()
    unclosed_result.scalar.return_value = 1
    audit_result = MagicMock()
    audit_result.scalar.return_value = 9999

    session = MagicMock()
    session.close = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[in_retention_result, pending_purge_result, unclosed_result, audit_result]
    )

    with patch.object(data_retention, "async_session_factory") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = report_retention_status()

    assert result["status"] == "ok"
    assert result["in_retention_employees"] == 12
    assert result["pending_purge_employees"] == 3
    assert result["unclosed_warnings"] == 1
    assert result["audit_logs_in_retention"] == 9999
    assert result["retention_policy"]["employees_years"] == RETENTION_YEARS_AFTER_LEAVE
    assert result["retention_policy"]["audit_logs_years"] == RETENTION_YEARS_WARNINGS


# ============================================================
# 3. _list_purge_candidates 测试
# ============================================================


@pytest.mark.asyncio
async def test_list_purge_candidates_excludes_unclosed_warnings():
    """有未结申诉的员工应被排除."""

    emp_ok = _FakeEmployee(leave_date=date.today() - timedelta(days=800))
    emp_unclosed = _FakeEmployee(leave_date=date.today() - timedelta(days=900))

    # 模拟 session.execute 返回不同结果
    session = MagicMock()
    session.close = AsyncMock()

    candidates_result = MagicMock()
    candidates_result.scalars.return_value.all.return_value = [emp_ok, emp_unclosed]

    unclosed_result = MagicMock()
    unclosed_result.all.return_value = [(emp_unclosed.id,)]

    session.execute = AsyncMock(side_effect=[candidates_result, unclosed_result])

    with patch.object(data_retention, "async_session_factory") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        candidates = await _list_purge_candidates(tenant_id=None)

    # emp_unclosed 应被排除
    assert emp_ok in candidates
    assert emp_unclosed not in candidates
    assert len(candidates) == 1


@pytest.mark.asyncio
async def test_list_purge_candidates_empty_returns_empty():
    """无候选返回空列表."""
    session = MagicMock()
    session.close = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_result)

    with patch.object(data_retention, "async_session_factory") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        candidates = await _list_purge_candidates(tenant_id=None)

    assert candidates == []


# ============================================================
# 4. _purge_employee_data 测试
# ============================================================


@pytest.mark.asyncio
async def test_purge_employee_data_deletes_in_correct_order():
    """删除顺序：warning_events → warnings → risk_predictions → employee，并写 audit_log."""
    emp = _FakeEmployee(leave_date=date.today() - timedelta(days=800))

    # 模拟 session
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    warning_ids_result = MagicMock()
    warning_ids_result.all.return_value = [(uuid4(),), (uuid4(),)]

    we_delete_result = MagicMock()
    we_delete_result.rowcount = 2

    w_delete_result = MagicMock()
    w_delete_result.rowcount = 2

    rp_delete_result = MagicMock()
    rp_delete_result.rowcount = 5

    emp_delete_result = MagicMock()
    emp_delete_result.rowcount = 1

    session.execute = AsyncMock(
        side_effect=[
            warning_ids_result,
            we_delete_result,
            w_delete_result,
            rp_delete_result,
            emp_delete_result,
        ]
    )

    with patch.object(data_retention, "async_session_factory") as factory, \
         patch.object(data_retention, "append_audit_log", new=AsyncMock()) as mock_audit:
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await _purge_employee_data(emp)

    assert result["warning_events"] == 2
    assert result["warnings"] == 2
    assert result["risk_predictions"] == 5
    assert result["employee"] == 1
    mock_audit.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_employee_data_rolls_back_on_error():
    """删除异常应回滚并重新抛出."""
    emp = _FakeEmployee(leave_date=date.today() - timedelta(days=800))

    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("deadlock"))

    with patch.object(data_retention, "async_session_factory") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        with pytest.raises(RuntimeError, match="deadlock"):
            await _purge_employee_data(emp)

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


# ============================================================
# 5. Celery 任务注册验证
# ============================================================


def test_purge_task_registered_in_celery():
    """任务应在 Celery 注册."""
    from app.celery_app import celery_app

    assert "app.tasks.data_retention.purge_departed_employees" in celery_app.tasks
    assert "app.tasks.data_retention.report_retention_status" in celery_app.tasks


def test_beat_schedule_includes_data_retention():
    """beat 调度应包含数据保留任务."""
    from app.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "data-retention-purge-weekly" in schedule
    assert schedule["data-retention-purge-weekly"]["task"] == "app.tasks.data_retention.purge_departed_employees"
    assert "data-retention-report-monthly" in schedule
    assert schedule["data-retention-report-monthly"]["task"] == "app.tasks.data_retention.report_retention_status"
