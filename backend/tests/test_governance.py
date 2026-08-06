"""W5 模型治理与 LLM 集成测试 - Kill Switch / 漂移检测 / 治理任务 / RiskService 熔断.

覆盖：
  - kill_switch：activate → is_active True → deactivate → is_active False（含 Redis 不可用降级）
  - drift_detector：compute_psi 相同分布 < 0.1；显著偏移 > 0.2
  - drift_detector：detect_drift_features 返回每列 status
  - detect_drift Celery 任务：能执行（降级路径也可）
  - fairness_daily_report 任务：能执行
  - auto_rollback 任务：能执行
  - Kill Switch 激活后 RiskService 返回降级结果
"""
from __future__ import annotations

from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from app.core import kill_switch
from app.ml.drift_detector import (
    PSI_CRITICAL_THRESHOLD,
    PSI_STABLE_THRESHOLD,
    compute_kl,
    compute_psi,
    detect_drift_features,
    detect_drift_summary,
)
from app.services.risk_service import RiskService

# ===== Fake Redis（用于 kill_switch 流程测试） =====


class _FakeSyncRedis:
    """同步 Redis 替身（内存 dict 存储）."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str):
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._store[key] = value
        return True

    def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0


class _FakeAsyncRedis:
    """异步 Redis 替身（内存 dict 存储）."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0

    async def close(self) -> None:
        pass


# ===== 1. Kill Switch 测试 =====


def test_kill_switch_failopen_when_redis_unavailable(monkeypatch):
    """Redis 不可用时 is_active() 返回 False（fail-open，不阻塞服务）."""
    # 强制 sync Redis 单例为不可用（返回 None）
    monkeypatch.setattr(kill_switch, "_get_sync_redis", lambda: None)

    # is_active 应返回 False（fail-open）
    assert kill_switch.is_active() is False

    # get_status 返回 inactive 默认 dict
    status = kill_switch.get_status()
    assert status["active"] is False
    assert status["reason"] == ""
    assert status["activated_at"] == ""
    assert status["activated_by"] == ""

    # activate / deactivate 不应抛异常（静默失败）
    kill_switch.activate(reason="test", operator_id="op1")
    kill_switch.deactivate(operator_id="op1")

    # 仍为 inactive
    assert kill_switch.is_active() is False


def test_kill_switch_activate_deactivate_flow_sync(monkeypatch):
    """activate → is_active True → deactivate → is_active False（同步，fake Redis）."""
    fake = _FakeSyncRedis()
    monkeypatch.setattr(kill_switch, "_get_sync_redis", lambda: fake)

    # 初始状态：未激活
    assert kill_switch.is_active() is False

    # 激活
    kill_switch.activate(reason="drift critical", operator_id="admin-001")
    assert kill_switch.is_active() is True

    # 状态查询
    status = kill_switch.get_status()
    assert status["active"] is True
    assert status["reason"] == "drift critical"
    assert status["activated_by"] == "admin-001"
    assert status["activated_at"] != ""

    # 解除
    kill_switch.deactivate(operator_id="admin-002")
    assert kill_switch.is_active() is False

    # 解除后状态
    status = kill_switch.get_status()
    assert status["active"] is False


async def test_kill_switch_activate_deactivate_flow_async(monkeypatch):
    """activate → is_active True → deactivate → is_active False（异步，fake Redis）."""
    fake = _FakeAsyncRedis()
    # 异步版本通过 app.core.redis.get_redis 获取客户端
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "get_redis", lambda: fake)

    # 初始未激活
    assert await kill_switch.is_active_async() is False

    # 激活
    await kill_switch.activate_async(reason="fairness violation", operator_id="admin-async")
    assert await kill_switch.is_active_async() is True

    status = await kill_switch.get_status_async()
    assert status["active"] is True
    assert status["reason"] == "fairness violation"
    assert status["activated_by"] == "admin-async"

    # 解除
    await kill_switch.deactivate_async(operator_id="admin-async")
    assert await kill_switch.is_active_async() is False


# ===== 2. drift_detector 测试 =====


def test_compute_psi_same_distribution_below_threshold():
    """相同分布的 PSI 应 < 0.1（stable）."""
    rng = np.random.default_rng(42)
    baseline = rng.normal(0, 1, 2000)
    current = rng.normal(0, 1, 2000)
    psi = compute_psi(baseline, current, n_bins=10)
    assert psi < PSI_STABLE_THRESHOLD, f"相同分布 PSI={psi:.4f} 应 < {PSI_STABLE_THRESHOLD}"


def test_compute_psi_shifted_distribution_above_critical():
    """显著偏移的分布 PSI 应 > 0.2（critical）."""
    rng = np.random.default_rng(42)
    baseline = rng.normal(0, 1, 2000)
    # 均值偏移 2 个标准差 → 显著漂移
    current = rng.normal(2, 1, 2000)
    psi = compute_psi(baseline, current, n_bins=10)
    assert psi > PSI_CRITICAL_THRESHOLD, f"显著偏移 PSI={psi:.4f} 应 > {PSI_CRITICAL_THRESHOLD}"


def test_compute_psi_nonnegative():
    """PSI 应非负."""
    rng = np.random.default_rng(7)
    baseline = rng.normal(0, 1, 500)
    current = rng.normal(0.5, 1.2, 500)
    psi = compute_psi(baseline, current)
    assert psi >= 0.0


def test_compute_psi_empty_arrays_returns_zero():
    """空数组应返回 0（不崩溃）."""
    psi = compute_psi(np.array([]), np.array([]))
    assert psi == 0.0


def test_compute_kl_same_distribution_near_zero():
    """相同分布的 KL 散度应接近 0."""
    rng = np.random.default_rng(42)
    baseline = rng.normal(0, 1, 2000)
    current = rng.normal(0, 1, 2000)
    kl = compute_kl(baseline, current, n_bins=10)
    assert kl >= 0.0
    # 相同分布 KL 应较小
    assert kl < 1.0


def test_compute_kl_shifted_distribution_positive():
    """偏移分布 KL 散度应为正且较大."""
    rng = np.random.default_rng(42)
    baseline = rng.normal(0, 1, 2000)
    current = rng.normal(3, 1, 2000)
    kl = compute_kl(baseline, current, n_bins=10)
    assert kl > 0.1


def test_detect_drift_features_returns_status_per_column():
    """detect_drift_features 应为每列返回 status."""
    rng = np.random.default_rng(42)
    # 构造 baseline / current：col_a 相同分布，col_b 显著偏移
    baseline = pd.DataFrame({
        "col_a": rng.normal(0, 1, 1000),
        "col_b": rng.normal(0, 1, 1000),
    })
    current = pd.DataFrame({
        "col_a": rng.normal(0, 1, 1000),
        "col_b": rng.normal(3, 1, 1000),  # 显著偏移
    })

    results = detect_drift_features(baseline, current, ["col_a", "col_b"], n_bins=10)
    assert len(results) == 2
    features = {r["feature"]: r for r in results}
    # col_a 应 stable
    assert features["col_a"]["status"] == "stable"
    assert features["col_a"]["psi"] < PSI_STABLE_THRESHOLD
    # col_b 应 critical
    assert features["col_b"]["status"] == "critical"
    assert features["col_b"]["psi"] > PSI_CRITICAL_THRESHOLD
    # 每项都含必要字段
    for r in results:
        assert "feature" in r
        assert "psi" in r
        assert "kl" in r
        assert "status" in r
        assert r["status"] in {"stable", "warning", "critical"}


def test_detect_drift_features_skips_missing_columns():
    """列不存在时应跳过（不阻塞）."""
    rng = np.random.default_rng(42)
    baseline = pd.DataFrame({"col_a": rng.normal(0, 1, 500)})
    current = pd.DataFrame({"col_a": rng.normal(0, 1, 500)})
    # 请求检测不存在的列
    results = detect_drift_features(baseline, current, ["nonexistent_col"])
    assert results == []


def test_detect_drift_summary_returns_aggregate():
    """detect_drift_summary 应返回汇总信息."""
    rng = np.random.default_rng(42)
    baseline = pd.DataFrame({
        "col_a": rng.normal(0, 1, 1000),
        "col_b": rng.normal(0, 1, 1000),
    })
    current = pd.DataFrame({
        "col_a": rng.normal(0, 1, 1000),
        "col_b": rng.normal(3, 1, 1000),
    })

    summary = detect_drift_summary(baseline, current, ["col_a", "col_b"], n_bins=10)
    assert "max_psi" in summary
    assert "critical_features" in summary
    assert "warning_features" in summary
    assert "summary" in summary
    assert "passed" in summary
    assert "features" in summary
    assert summary["passed"] is False  # col_b critical → 不通过
    assert "col_b" in summary["critical_features"]
    assert summary["max_psi"] > PSI_CRITICAL_THRESHOLD


def test_detect_drift_summary_passed_when_stable():
    """所有特征稳定时 summary.passed 应为 True."""
    rng = np.random.default_rng(42)
    baseline = pd.DataFrame({"col_a": rng.normal(0, 1, 2000)})
    current = pd.DataFrame({"col_a": rng.normal(0, 1, 2000)})
    summary = detect_drift_summary(baseline, current, ["col_a"], n_bins=10)
    assert summary["passed"] is True
    assert summary["critical_features"] == []
    assert summary["max_psi"] < PSI_CRITICAL_THRESHOLD


# ===== 3. Celery 治理任务测试 =====


def test_detect_drift_celery_task_executes():
    """detect_drift Celery 任务应能执行（不崩溃）.

    数据文件存在时返回 status=ok；不存在时返回 status=skipped。
    两种情况都不应抛异常。
    """
    from app.tasks.model_governance import detect_drift

    # 直接调用任务对象（同步执行，不经过 Celery broker）
    result = detect_drift()
    assert isinstance(result, dict)
    assert "status" in result
    assert "checked_at" in result
    # status 应为 ok 或 skipped
    assert result["status"] in {"ok", "skipped"}
    if result["status"] == "ok":
        assert "max_psi" in result
        assert "critical_features" in result
        assert "passed" in result
        assert isinstance(result["max_psi"], float)
        assert isinstance(result["critical_features"], list)


def test_fairness_daily_report_celery_task_executes():
    """fairness_daily_report Celery 任务应能执行（不崩溃）."""
    from app.tasks.model_governance import fairness_daily_report

    result = fairness_daily_report()
    assert isinstance(result, dict)
    assert "status" in result
    assert "checked_at" in result
    assert result["status"] in {"ok", "skipped"}
    if result["status"] == "ok":
        assert "max_deviation" in result
        assert "dimensions" in result
        assert "kill_switch_activated" in result
        assert isinstance(result["kill_switch_activated"], bool)


def test_auto_rollback_celery_task_executes():
    """auto_rollback Celery 任务应能执行（不崩溃）."""
    from app.tasks.model_governance import auto_rollback

    result = auto_rollback()
    assert isinstance(result, dict)
    assert "status" in result
    assert "checked_at" in result
    assert "rolled_back" in result
    assert isinstance(result["rolled_back"], bool)


# ===== 4. RiskService Kill Switch 熔断测试 =====


async def test_risk_service_predict_returns_degraded_when_kill_switch_active(monkeypatch):
    """Kill Switch 激活时 RiskService.predict 返回安全降级结果."""
    # Mock kill_switch.is_active_async 返回 True
    async def _mock_active():
        return True

    # 在 risk_service 模块内部导入的位置 patch（动态 import）
    # patch is_active_async 在 kill_switch 模块
    monkeypatch.setattr(kill_switch, "is_active_async", _mock_active)

    emp_id = uuid4()
    tenant_id = uuid4()

    # 调用 predict（kill switch 检查在 db 检查之前，所以无需 db）
    result = await RiskService.predict(emp_id, tenant_id, db=None)

    # 验证降级结果
    assert result["kill_switch"] is True
    assert result["risk_score"] == 50
    assert result["risk_level"] == "medium"
    assert result["model_version"] == "kill-switch-active"
    assert result["cached"] is False
    assert result["shap_factors"] == []
    assert result["employee_id"] == str(emp_id)
    assert result["prediction_id"] is None


async def test_risk_service_predict_kill_switch_check_failure_does_not_block(monkeypatch):
    """Kill Switch 检查异常时不阻塞主流程（fail-open，继续走正常预测路径）.

    由于无 db 会话，正常路径会抛 ValueError（需要 db 查 Employee）。
    验证：kill_switch 检查异常后，仍尝试走正常路径（抛 ValueError 而非 kill switch 异常）。
    """
    async def _mock_raises():
        raise RuntimeError("redis connection error")

    monkeypatch.setattr(kill_switch, "is_active_async", _mock_raises)

    emp_id = uuid4()
    tenant_id = uuid4()

    # kill switch 检查异常 → fail-open → 继续正常流程 → 无 db → ValueError
    with pytest.raises(ValueError, match="db"):
        await RiskService.predict(emp_id, tenant_id, db=None)


# ===== 5. admin schemas 测试 =====


def test_admin_schemas_kill_switch_status():
    """KillSwitchStatus schema 应正确序列化."""
    from app.schemas.admin import KillSwitchAction, KillSwitchStatus

    status = KillSwitchStatus(active=True, reason="test", activated_at="2026-01-01T00:00:00Z", activated_by="admin")
    assert status.active is True
    assert status.reason == "test"

    # 默认值
    status_default = KillSwitchStatus(active=False)
    assert status_default.reason == ""
    assert status_default.activated_at == ""

    action = KillSwitchAction(reason="drift critical")
    assert action.reason == "drift critical"


# ===== 6. 治理任务降级路径测试（无数据文件时） =====


def test_detect_drift_returns_skipped_when_baseline_missing(monkeypatch, tmp_path):
    """基线文件不存在时 detect_drift 返回 status=skipped."""
    from app.tasks import model_governance

    # patch 路径常量指向不存在的目录
    nonexistent = tmp_path / "nonexistent.csv"
    monkeypatch.setattr(model_governance, "_BASELINE_PATH", nonexistent)
    monkeypatch.setattr(model_governance, "_CURRENT_FALLBACK_PATH", nonexistent)

    result = model_governance.detect_drift()
    assert result["status"] == "skipped"
    assert "reason" in result


def test_fairness_daily_report_returns_skipped_when_data_missing(monkeypatch, tmp_path):
    """公平性数据文件不存在时 fairness_daily_report 返回 status=skipped."""
    from app.tasks import model_governance

    nonexistent = tmp_path / "nonexistent.csv"
    monkeypatch.setattr(model_governance, "_FAIRNESS_DATA_PATH", nonexistent)

    result = model_governance.fairness_daily_report()
    assert result["status"] == "skipped"
    assert "reason" in result
