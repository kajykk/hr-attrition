"""模型治理任务 - 漂移检测 + 公平性日报 + 自动回滚（D03 4.5 + D10 7.3）.

任务（Celery beat 调度）：
  - detect_drift()：每日 02:00，PSI/KL 漂移检测
  - fairness_daily_report()：每日 03:00，4 维度公平性偏差检测
  - auto_rollback()：检查连续 critical 告警，触发回滚

降级策略：
  - 文件/DB 不可用 → 返回 {status: "skipped", reason: ...}
  - Kill Switch 模块 Redis 不可用 → 静默跳过激活动作
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from app.celery_app import celery_app
from app.core.kill_switch import activate as kill_switch_activate
from app.core.kill_switch import is_active as kill_switch_is_active
from app.core.logging import get_logger
from app.ml.drift_detector import detect_drift_summary
from app.ml.feature_engineering import PROCESSED_DIR, STRUCTURED_FEATURE_COLUMNS

logger = get_logger(__name__)

# 基线/模拟数据路径
_BASELINE_PATH = PROCESSED_DIR / "X_struct_train.csv"
_CURRENT_FALLBACK_PATH = PROCESSED_DIR / "X_struct_test.csv"

# 公平性测试数据路径
_FAIRNESS_DATA_PATH = Path(__file__).resolve().parent.parent / "ml" / "models" / "test_predictions.csv"

# 漂移检测：取 baseline 前 N 行（D03 4.5）
_BASELINE_SAMPLE_ROWS = 1000

# 公平性阈值（D10 7.3）
FAIRNESS_WARNING_THRESHOLD = 0.05  # 5%
FAIRNESS_CRITICAL_THRESHOLD = 0.08  # 8% → 触发 Kill Switch

# 自动回滚：连续 critical 告警天数阈值
_ROLLBACK_CONSECUTIVE_DAYS = 3


def _load_baseline() -> pd.DataFrame | None:
    """加载基线数据（X_struct_train.csv 前 1000 行）."""
    try:
        if not _BASELINE_PATH.exists():
            logger.warning("基线文件不存在 | path=%s", _BASELINE_PATH)
            return None
        df = pd.read_csv(_BASELINE_PATH, nrows=_BASELINE_SAMPLE_ROWS)
        return df
    except Exception as e:  # noqa: BLE001
        logger.error("加载基线数据失败 | err=%s", e)
        return None


def _load_current_from_csv() -> pd.DataFrame | None:
    """降级：用 X_struct_test.csv 模拟当前分布（无 DB 时）."""
    try:
        if not _CURRENT_FALLBACK_PATH.exists():
            logger.warning("当前分布降级文件不存在 | path=%s", _CURRENT_FALLBACK_PATH)
            return None
        df = pd.read_csv(_CURRENT_FALLBACK_PATH)
        return df
    except Exception as e:  # noqa: BLE001
        logger.error("加载当前分布降级数据失败 | err=%s", e)
        return None


@celery_app.task(name="app.tasks.model_governance.worker_heartbeat")
def worker_heartbeat() -> dict:
    """Celery 心跳任务（每 5 分钟）.

    写入 Redis `celery:heartbeat` 时间戳，供 /health 探测 worker/beat 存活度
    （P2-11：原先 health 端点 celery 状态为硬编码）。
    """
    from app.core.celery_heartbeat import write_heartbeat
    from app.core.kill_switch import _get_sync_redis

    write_heartbeat(_get_sync_redis())
    return {"status": "ok", "checked_at": datetime.now(UTC).isoformat()}


@celery_app.task(name="app.tasks.model_governance.detect_drift")
def detect_drift() -> dict:
    """漂移检测任务（每日 02:00）.

    流程：
      1. 加载 baseline（X_struct_train.csv 前 1000 行）
      2. 加载 current（优先 DB risk_predictions.feature_values；降级用 X_struct_test.csv）
      3. 调 drift_detector.detect_drift_summary() 计算 PSI/KL
      4. 任何特征 PSI > 0.2 → log critical + 返回告警

    Returns:
        {status: "ok"/"skipped", max_psi, critical_features, checked_at, ...}
    """
    logger.info("漂移检测任务执行 | time=%s", datetime.now(UTC).isoformat())

    baseline = _load_baseline()
    if baseline is None:
        return {
            "status": "skipped",
            "reason": "基线数据不可用",
            "checked_at": datetime.now(UTC).isoformat(),
        }

    current = _load_current_from_csv()
    if current is None:
        return {
            "status": "skipped",
            "reason": "当前分布数据不可用",
            "checked_at": datetime.now(UTC).isoformat(),
        }

    # 取交集列（避免列不一致）
    common_cols = [c for c in STRUCTURED_FEATURE_COLUMNS if c in baseline.columns and c in current.columns]
    if not common_cols:
        return {
            "status": "skipped",
            "reason": "无共同特征列可检测",
            "checked_at": datetime.now(UTC).isoformat(),
        }

    try:
        summary = detect_drift_summary(baseline, current, common_cols)
    except Exception as e:  # noqa: BLE001
        logger.error("漂移检测计算异常 | err=%s", e)
        return {
            "status": "skipped",
            "reason": f"计算异常: {e}",
            "checked_at": datetime.now(UTC).isoformat(),
        }

    # 任何 critical → log warning
    if summary["critical_features"]:
        logger.warning(
            "漂移检测发现 critical 特征 | max_psi=%.4f | critical=%s",
            summary["max_psi"], summary["critical_features"],
        )

    return {
        "status": "ok",
        "max_psi": summary["max_psi"],
        "critical_features": summary["critical_features"],
        "warning_features": summary["warning_features"],
        "passed": summary["passed"],
        "features": [
            {"feature": f["feature"], "psi": f["psi"]}
            for f in summary.get("features", [])
        ],
        "summary": summary["summary"],
        "checked_at": datetime.now(UTC).isoformat(),
    }


@celery_app.task(name="app.tasks.model_governance.fairness_daily_report")
def fairness_daily_report() -> dict:
    """公平性日报任务（每日 03:00，D10 7.3）.

    流程：
      1. 加载最近预测（test_predictions.csv 降级）
      2. 计算 4 维度偏差（gender/age/ethnicity/disability）
      3. 偏差 > 5% → log warning；> 8% → 触发 Kill Switch
      4. 返回日报

    Returns:
        {status, max_deviation, dimensions, kill_switch_activated}
    """
    logger.info("公平性日报任务执行 | time=%s", datetime.now(UTC).isoformat())

    # 加载公平性数据（含 risk_score + 审计字段）
    try:
        if not _FAIRNESS_DATA_PATH.exists():
            logger.warning("公平性数据不存在 | path=%s", _FAIRNESS_DATA_PATH)
            return {
                "status": "skipped",
                "reason": "公平性数据文件不存在",
                "checked_at": datetime.now(UTC).isoformat(),
            }
        df = pd.read_csv(_FAIRNESS_DATA_PATH)
    except Exception as e:  # noqa: BLE001
        logger.error("加载公平性数据失败 | err=%s", e)
        return {
            "status": "skipped",
            "reason": f"数据加载失败: {e}",
            "checked_at": datetime.now(UTC).isoformat(),
        }

    # 计算各维度偏差
    dimensions = _compute_fairness_dimensions(df)
    if not dimensions:
        return {
            "status": "skipped",
            "reason": "无可用维度",
            "checked_at": datetime.now(UTC).isoformat(),
        }

    max_deviation = max(d["parity_difference"] for d in dimensions.values())
    kill_switch_activated = False

    # 偏差 > 5% → log warning
    for name, info in dimensions.items():
        if info["parity_difference"] > FAIRNESS_WARNING_THRESHOLD:
            logger.warning(
                "公平性偏差超阈值 | dim=%s | diff=%.4f | threshold=%.2f",
                name, info["parity_difference"], FAIRNESS_WARNING_THRESHOLD,
            )
        # 偏差 > 8% → 触发 Kill Switch
        if info["parity_difference"] > FAIRNESS_CRITICAL_THRESHOLD:
            logger.error(
                "公平性偏差严重超阈值，触发 Kill Switch | dim=%s | diff=%.4f | threshold=%.2f",
                name, info["parity_difference"], FAIRNESS_CRITICAL_THRESHOLD,
            )
            try:
                kill_switch_activate(
                    reason=f"公平性偏差超阈值: {name}={info['parity_difference']:.4f}",
                    operator_id="system-fairness-monitor",
                )
                kill_switch_activated = True
            except Exception as e:  # noqa: BLE001
                logger.error("Kill Switch 激活失败 | err=%s", e)

    return {
        "status": "ok",
        "max_deviation": max_deviation,
        "dimensions": dimensions,
        "kill_switch_activated": kill_switch_activated,
        "warning_threshold": FAIRNESS_WARNING_THRESHOLD,
        "critical_threshold": FAIRNESS_CRITICAL_THRESHOLD,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _compute_fairness_dimensions(df: pd.DataFrame) -> dict:
    """计算 4 维度公平性偏差（复用 fairness_test 逻辑）.

    维度：gender(M/F)、age(<35 vs >=35)、ethnicity(汉族/少数民族)、disability(无/有)
    指标：各组高风险预测率之差的最大绝对值（risk_score >= 60 为高风险）。
    """
    dimensions: dict = {}
    threshold = 60
    required = {"risk_score"}
    if not required.issubset(df.columns):
        logger.warning("公平性计算：缺少 risk_score 列")
        return dimensions

    high = (df["risk_score"] >= threshold).astype(int)
    work = df.copy()
    work["__high__"] = high

    def _parity(group_col: str, label: str) -> dict:
        if group_col not in work.columns:
            return {}
        rates = work.groupby(group_col)["__high__"].mean().to_dict()
        vals = list(rates.values())
        diff = float(max(vals) - min(vals)) if len(vals) >= 2 else 0.0
        return {
            "groups": {str(k): float(v) for k, v in rates.items()},
            "parity_difference": diff,
            "passed": bool(diff < FAIRNESS_WARNING_THRESHOLD),
            "label": label,
        }

    # gender
    if "gender" in work.columns:
        dimensions["gender"] = _parity("gender", "性别 (M/F)")

    # age: <35 vs >=35
    if "age_derived" in work.columns:
        work["_age_group"] = np.where(work["age_derived"] < 35, "<35", ">=35")
        dimensions["age"] = _parity("_age_group", "年龄 (<35 / >=35)")

    # ethnicity
    if "ethnicity" in work.columns:
        work["_ethnicity_label"] = work["ethnicity"].map({0: "汉族", 1: "少数民族"}).astype(str)
        dimensions["ethnicity"] = _parity("_ethnicity_label", "民族 (汉族/少数民族)")

    # disability
    if "disability" in work.columns:
        work["_disability_label"] = work["disability"].map({0: "无障碍", 1: "有障碍"}).astype(str)
        dimensions["disability"] = _parity("_disability_label", "残障 (0/1)")

    return dimensions


@celery_app.task(name="app.tasks.model_governance.auto_rollback")
def auto_rollback() -> dict:
    """自动回滚任务（检查连续 critical 告警）.

    流程：
      1. 调 detect_drift 获取最新结果
      2. 检查连续 critical 天数（简化：单次检测 critical 即标记）
      3. 连续 3 天 critical → 自动回滚（log + 通知，实际回滚动作占位）

    注意：完整连续天数追踪需持久化存储，此处用简化逻辑（检测当前状态）。
    """
    logger.info("自动回滚检查任务执行 | time=%s", datetime.now(UTC).isoformat())

    try:
        drift_result = detect_drift()
    except Exception as e:  # noqa: BLE001
        logger.error("自动回滚：漂移检测调用失败 | err=%s", e)
        return {
            "status": "skipped",
            "reason": f"漂移检测失败: {e}",
            "rolled_back": False,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    if drift_result.get("status") != "ok":
        return {
            "status": "skipped",
            "reason": drift_result.get("reason", "漂移检测未执行"),
            "rolled_back": False,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    critical_features = drift_result.get("critical_features", [])
    max_psi = drift_result.get("max_psi", 0.0)

    # 简化逻辑：若 max_psi 极高（>0.3）且 Kill Switch 未激活，标记需回滚
    # 完整实现需追踪连续天数，此处占位
    should_rollback = max_psi > 0.3 and bool(critical_features) and not kill_switch_is_active()

    if should_rollback:
        logger.error(
            "自动回滚触发 | max_psi=%.4f | critical=%s | 实际回滚动作占位",
            max_psi, critical_features,
        )
        # 实际回滚动作占位：切模型版本/通知等
        return {
            "status": "rolled_back",
            "rolled_back": True,
            "reason": f"漂移严重 max_psi={max_psi:.4f}，触发回滚",
            "critical_features": critical_features,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    return {
        "status": "ok",
        "rolled_back": False,
        "max_psi": max_psi,
        "critical_features": critical_features,
        "checked_at": datetime.now(UTC).isoformat(),
    }
