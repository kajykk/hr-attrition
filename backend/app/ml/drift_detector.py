"""漂移检测 - PSI（Population Stability Index）+ KL 散度（D03 4.5）.

阈值规则（D03 4.5）：
  PSI < 0.1       → stable（稳定）
  0.1 <= PSI < 0.2 → warning（轻微漂移，关注）
  PSI >= 0.2       → critical（显著漂移，触发告警/回滚）

PSI 计算流程：
  1. 对 baseline 等频分桶（n_bins），得到分桶边界
  2. current 用相同边界计算各桶比例
  3. PSI = Σ (current_pct - baseline_pct) * ln(current_pct / baseline_pct)
  4. 比例加 1e-6 平滑避免除零

KL 散度：
  KL = Σ baseline_pct * ln(baseline_pct / current_pct)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)

# 平滑常量（避免除零/ln(0)）
_EPS = 1e-6

# 状态阈值（D03 4.5）
PSI_STABLE_THRESHOLD = 0.1
PSI_CRITICAL_THRESHOLD = 0.2


def _compute_bin_edges(arr: np.ndarray, n_bins: int) -> np.ndarray:
    """对 baseline 计算等频分桶边界（含 -inf/inf 两端）.

    返回 n_bins+1 个边界点，使每桶约含相同数量的样本。
    """
    # 用分位数计算边界（等频分桶）
    quantiles = np.linspace(0, 1, n_bins + 1)
    edges = np.quantile(arr, quantiles)
    # 去重（相同值会合并边界），确保边界唯一
    edges = np.unique(edges)
    if len(edges) < 2:
        # 退化情况（所有值相同），用单桶
        edges = np.array([-np.inf, np.inf])
    else:
        # 首尾用 ±inf 覆盖所有可能值
        edges[0] = -np.inf
        edges[-1] = np.inf
    return edges


def _bin_proportions(arr: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """按指定边界分桶，返回各桶比例（加 _EPS 平滑）."""
    # np.histogram 默认含右端点（除最后一桶），与分桶语义一致
    counts, _ = np.histogram(arr, bins=edges)
    total = counts.sum()
    if total == 0:
        # 空数组，均匀分布
        props = np.ones(len(counts)) / len(counts)
    else:
        props = counts / total
    # 平滑：避免除零/ln(0)
    props = props + _EPS
    # 重新归一化
    props = props / props.sum()
    return props


def compute_psi(baseline: np.ndarray, current: np.ndarray, n_bins: int = 10) -> float:
    """计算 PSI（Population Stability Index）.

    Args:
        baseline: 基线分布（一维数组）
        current: 当前分布（一维数组）
        n_bins: 分桶数（默认 10）

    Returns:
        PSI 值（float）。相同分布接近 0，差异越大 PSI 越大。
    """
    baseline = np.asarray(baseline, dtype=float).ravel()
    current = np.asarray(current, dtype=float).ravel()
    # 过滤 NaN
    baseline = baseline[~np.isnan(baseline)]
    current = current[~np.isnan(current)]
    if len(baseline) == 0 or len(current) == 0:
        return 0.0

    # 用 baseline 计算分桶边界
    edges = _compute_bin_edges(baseline, n_bins)
    base_props = _bin_proportions(baseline, edges)
    curr_props = _bin_proportions(current, edges)

    # PSI = Σ (curr - base) * ln(curr / base)
    psi = float(np.sum((curr_props - base_props) * np.log(curr_props / base_props)))
    return psi


def compute_kl(baseline: np.ndarray, current: np.ndarray, n_bins: int = 10) -> float:
    """计算 KL 散度 KL(baseline || current).

    Args:
        baseline: 基线分布
        current: 当前分布
        n_bins: 分桶数

    Returns:
        KL 散度值（float），相同分布接近 0。
    """
    baseline = np.asarray(baseline, dtype=float).ravel()
    current = np.asarray(current, dtype=float).ravel()
    baseline = baseline[~np.isnan(baseline)]
    current = current[~np.isnan(current)]
    if len(baseline) == 0 or len(current) == 0:
        return 0.0

    edges = _compute_bin_edges(baseline, n_bins)
    base_props = _bin_proportions(baseline, edges)
    curr_props = _bin_proportions(current, edges)

    # KL(baseline || current) = Σ base * ln(base / curr)
    kl = float(np.sum(base_props * np.log(base_props / curr_props)))
    return kl


def _classify_status(psi: float) -> str:
    """根据 PSI 值分类状态（D03 4.5）."""
    if psi < PSI_STABLE_THRESHOLD:
        return "stable"
    if psi < PSI_CRITICAL_THRESHOLD:
        return "warning"
    return "critical"


def detect_drift_features(
    baseline_df: pd.DataFrame,
    current_df: pd.DataFrame,
    columns: list[str],
    n_bins: int = 10,
) -> list[dict]:
    """对每列计算 PSI/KL，返回各特征漂移状态.

    Args:
        baseline_df: 基线 DataFrame
        current_df: 当前 DataFrame
        columns: 待检测的列名列表
        n_bins: 分桶数

    Returns:
        [{"feature": str, "psi": float, "kl": float, "status": "stable"/"warning"/"critical"}]
    """
    results: list[dict] = []
    for col in columns:
        if col not in baseline_df.columns or col not in current_df.columns:
            # 列不存在，跳过（不阻塞）
            logger.warning("漂移检测：列 %s 不存在，跳过", col)
            continue
        try:
            base_arr = baseline_df[col].to_numpy(dtype=float)
            curr_arr = current_df[col].to_numpy(dtype=float)
            psi = compute_psi(base_arr, curr_arr, n_bins=n_bins)
            kl = compute_kl(base_arr, curr_arr, n_bins=n_bins)
            status = _classify_status(psi)
            results.append({
                "feature": col,
                "psi": psi,
                "kl": kl,
                "status": status,
            })
            if status == "critical":
                logger.warning("漂移检测 [critical] | feature=%s | psi=%.4f", col, psi)
        except Exception as e:  # noqa: BLE001
            logger.error("漂移检测：列 %s 计算失败 | err=%s", col, e)
            results.append({
                "feature": col,
                "psi": 0.0,
                "kl": 0.0,
                "status": "stable",
            })
    return results


def detect_drift_summary(
    baseline_df: pd.DataFrame,
    current_df: pd.DataFrame,
    columns: list[str],
    n_bins: int = 10,
) -> dict:
    """漂移检测汇总.

    Args:
        baseline_df: 基线 DataFrame
        current_df: 当前 DataFrame
        columns: 待检测列
        n_bins: 分桶数

    Returns:
        {
            "max_psi": float,
            "critical_features": [str, ...],
            "warning_features": [str, ...],
            "summary": str,
            "passed": bool,  # max_psi < 0.2 时为 True
            "features": [{"feature", "psi", "kl", "status"}]
        }
    """
    features = detect_drift_features(baseline_df, current_df, columns, n_bins=n_bins)
    if not features:
        return {
            "max_psi": 0.0,
            "critical_features": [],
            "warning_features": [],
            "summary": "无特征可检测",
            "passed": True,
            "features": [],
        }
    max_psi = max(f["psi"] for f in features)
    critical = [f["feature"] for f in features if f["status"] == "critical"]
    warning = [f["feature"] for f in features if f["status"] == "warning"]
    passed = max_psi < PSI_CRITICAL_THRESHOLD
    summary = (
        f"检测 {len(features)} 个特征，max_psi={max_psi:.4f}，"
        f"critical={len(critical)}，warning={len(warning)}"
    )
    return {
        "max_psi": max_psi,
        "critical_features": critical,
        "warning_features": warning,
        "summary": summary,
        "passed": passed,
        "features": features,
    }
