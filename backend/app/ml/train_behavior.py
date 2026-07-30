"""T-305 IsolationForest 行为异常检测模型.

- 仅用行为时序特征训练异常检测（半监督：在"非离职"行为上学习正常模式）。
- 离职员工行为在后几个月呈现异常趋势（邮件下降、会议拒绝率上升），
  应被判定为更异常 → score_behavior 更高。
- score_behavior ∈ [0,1]：由 decision_function 取负后归一化。
- 目标：行为分对离职员工的区分度 AUC ≥ 0.65（行为是辅助模态）。
- 产物：models/behavior_if.pkl（含模型 + 归一化参数 + 特征列）。
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score

from app.ml.feature_engineering import load_split

MODELS_DIR = Path(__file__).resolve().parent / "models"
MODEL_PATH = MODELS_DIR / "behavior_if.pkl"
METRICS_PATH = MODELS_DIR / "behavior_metrics.json"

RANDOM_SEED = 42


def train() -> dict:
    """训练 IsolationForest 行为模型并保存，返回指标 dict."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    data = load_split()

    X_train = data["X_behav_train"]
    X_val = data["X_behav_val"]
    X_test = data["X_behav_test"]
    y_train = data["y_train"]
    y_test = data["y_test"]

    feature_cols = list(X_train.columns)

    # 半监督：在"非离职"行为上学习正常模式
    normal_mask = (y_train == 0).to_numpy()
    X_normal = X_train.loc[normal_mask]
    print(f"[T-305] 在 {len(X_normal)} 条非离职行为上训练 IsolationForest ...")

    # contamination 设为低值：正常数据中少量异常
    model = IsolationForest(
        n_estimators=300,
        max_samples="auto",
        contamination=float(y_train.mean()),  # 约等于离职率
        random_state=RANDOM_SEED,
        n_jobs=-1,
    )
    model.fit(X_normal)

    # decision_function：越大越正常；取负 → 越大越异常
    raw_train = -model.decision_function(X_train)
    raw_test = -model.decision_function(X_test)

    # 用训练集分位归一化到 [0,1]（更稳健）
    lo = float(np.percentile(raw_train, 1))
    hi = float(np.percentile(raw_train, 99))
    if hi <= lo:
        hi = lo + 1.0

    def _norm(arr):
        return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)

    score_test = _norm(raw_test)
    auc_test = roc_auc_score(y_test, score_test)

    # 离职员工平均分 vs 非离职平均分（区分度参考）
    mean_pos = float(score_test[y_test == 1].mean()) if (y_test == 1).any() else 0.0
    mean_neg = float(score_test[y_test == 0].mean()) if (y_test == 0).any() else 0.0

    artifact = {
        "model": model,
        "feature_columns": feature_cols,
        "norm_lo": lo,
        "norm_hi": hi,
    }
    joblib.dump(artifact, MODEL_PATH)

    metrics = {
        "auc_test": float(auc_test),
        "mean_score_attrition": mean_pos,
        "mean_score_non_attrition": mean_neg,
        "separation": mean_pos - mean_neg,
        "target_auc": 0.65,
        "passed": bool(auc_test >= 0.65),
    }
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print("[T-305] 行为模型测试集 AUC = {:.4f}（目标 ≥ 0.65，{}）".format(
        auc_test, "达标" if auc_test >= 0.65 else "未达标"))
    print(f"[T-305] 离职均分 {mean_pos:.3f} vs 非离职均分 {mean_neg:.3f}（区分度 {mean_pos-mean_neg:.3f}）")
    print(f"[T-305] 模型保存到 {MODEL_PATH}")
    return metrics


if __name__ == "__main__":
    train()
