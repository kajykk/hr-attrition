"""T-303 LightGBM 结构化离职风险模型.

- 二分类（Attrition），60/20/20 划分已由 feature_engineering 产出。
- 早停 50 轮（在 val 上监控 AUC）。
- 目标：测试集 AUC ≥ 0.85。
- 产物：models/structured_lgbm.pkl、structured_feature_importance.json、structured_metrics.json。
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    roc_auc_score,
)

from app.ml.feature_engineering import load_split

MODELS_DIR = Path(__file__).resolve().parent / "models"
MODEL_PATH = MODELS_DIR / "structured_lgbm.pkl"
IMPORTANCE_PATH = MODELS_DIR / "structured_feature_importance.json"
METRICS_PATH = MODELS_DIR / "structured_metrics.json"

EARLY_STOPPING_ROUNDS = 50
RANDOM_SEED = 42


def train() -> dict:
    """训练 LightGBM 结构化模型并保存产物，返回指标 dict."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    data = load_split()

    X_train = data["X_struct_train"]
    X_val = data["X_struct_val"]
    X_test = data["X_struct_test"]
    y_train = data["y_train"]
    y_val = data["y_val"]
    y_test = data["y_test"]

    feature_cols = list(X_train.columns)

    train_set = lgb.Dataset(X_train, label=y_train, feature_name=feature_cols)
    val_set = lgb.Dataset(X_val, label=y_val, feature_name=feature_cols, reference=train_set)

    pos, neg = int(y_train.sum()), int(len(y_train) - y_train.sum())
    scale_pos_weight = neg / max(pos, 1)

    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": -1,
        "min_child_samples": 30,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 0.1,
        "scale_pos_weight": scale_pos_weight,
        "verbose": -1,
        "seed": RANDOM_SEED,
    }

    print("[T-303] 训练 LightGBM（早停 50 轮，监控 val AUC）...")
    model = lgb.train(
        params,
        train_set,
        num_boost_round=2000,
        valid_sets=[train_set, val_set],
        valid_names=["train", "val"],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=True),
            lgb.log_evaluation(period=100),
        ],
    )

    # 测试集评估
    y_test_pred_proba = model.predict(X_test, num_iteration=model.best_iteration)
    auc_test = roc_auc_score(y_test, y_test_pred_proba)
    y_test_pred_label = (y_test_pred_proba >= 0.5).astype(int)
    report = classification_report(y_test, y_test_pred_label, output_dict=True, digits=4)
    cm = confusion_matrix(y_test, y_test_pred_label).tolist()

    # 特征重要性
    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(
        [{"feature": f, "importance_gain": float(v)} for f, v in zip(feature_cols, importance)],
        key=lambda x: x["importance_gain"],
        reverse=True,
    )

    # 保存产物
    artifact = {
        "model": model,
        "feature_columns": feature_cols,
        "best_iteration": int(model.best_iteration),
    }
    joblib.dump(artifact, MODEL_PATH)
    with open(IMPORTANCE_PATH, "w", encoding="utf-8") as f:
        json.dump(feat_imp, f, ensure_ascii=False, indent=2)

    metrics = {
        "auc_test": float(auc_test),
        "best_iteration": int(model.best_iteration),
        "classification_report": report,
        "confusion_matrix": cm,
        "target_auc": 0.85,
        "passed": bool(auc_test >= 0.85),
    }
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print("[T-303] 测试集 AUC = {:.4f}（目标 ≥ 0.85，{}）".format(
        auc_test, "达标" if auc_test >= 0.85 else "未达标"))
    print("[T-303] 分类报告：")
    print(classification_report(y_test, y_test_pred_label, digits=4))
    print("[T-303] 混淆矩阵 [TN FP; FN TP]：", cm)
    print(f"[T-303] 模型保存到 {MODEL_PATH}")
    print(f"[T-303] 特征重要性保存到 {IMPORTANCE_PATH}")
    return metrics


if __name__ == "__main__":
    train()
