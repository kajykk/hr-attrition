"""T-306 多模态融合引擎.

融合结构化（LightGBM）与行为（IsolationForest）模态分数：
  score_final = 0.7 * score_struct + 0.3 * score_behavior
  risk_score  = int(round(score_final * 100))   # 0-100
  risk_level  : <20 low, <40 medium_low, <60 medium, <80 medium_high, >=80 high

文本模态（T-304 MacBERT）暂未接入，权重临时归一为 structured:0.7 / behavior:0.3。

目标：融合后测试集 AUC ≥ 0.85；高风险前 20% 员工中离职召回率 ≥ 0.80。
产物：models/fusion_metrics.json、models/test_predictions.csv。
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, roc_auc_score

from app.core.logging import get_logger
from app.ml.feature_engineering import load_split

logger = get_logger(__name__)

MODELS_DIR = Path(__file__).resolve().parent / "models"
STRUCT_MODEL_PATH = MODELS_DIR / "structured_lgbm.pkl"
BEHAVIOR_MODEL_PATH = MODELS_DIR / "behavior_if.pkl"
FUSION_METRICS_PATH = MODELS_DIR / "fusion_metrics.json"
TEST_PREDICTIONS_PATH = MODELS_DIR / "test_predictions.csv"

# 融合权重（文本模态待 T-304 接入后改为 0.5/0.3/0.2）
WEIGHT_STRUCTURED = 0.7
WEIGHT_BEHAVIOR = 0.3

# 风险等级阈值
RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM_LOW = "medium_low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_MEDIUM_HIGH = "medium_high"
RISK_LEVEL_HIGH = "high"


def score_to_level(score: int) -> str:
    """风险分 → 等级（D04 4.1）."""
    if score >= 80:
        return RISK_LEVEL_HIGH
    if score >= 60:
        return RISK_LEVEL_MEDIUM_HIGH
    if score >= 40:
        return RISK_LEVEL_MEDIUM
    if score >= 20:
        return RISK_LEVEL_MEDIUM_LOW
    return RISK_LEVEL_LOW


class FusionEngine:
    """多模态融合引擎，加载已训练的结构化与行为模型."""

    def __init__(self) -> None:
        struct_artifact = joblib.load(STRUCT_MODEL_PATH)
        behavior_artifact = joblib.load(BEHAVIOR_MODEL_PATH)
        self.struct_model = struct_artifact["model"]
        self.struct_feature_columns = struct_artifact["feature_columns"]
        self.behavior_model = behavior_artifact["model"]
        self.behavior_feature_columns = behavior_artifact["feature_columns"]
        self.norm_lo = behavior_artifact["norm_lo"]
        self.norm_hi = behavior_artifact["norm_hi"]

    # ----- 模态打分 -----
    def score_struct(self, X_struct: pd.DataFrame) -> np.ndarray:
        X = self._align(X_struct, self.struct_feature_columns)
        return np.asarray(self.struct_model.predict(X), dtype=float)

    def score_behavior(self, X_behavior: pd.DataFrame) -> np.ndarray:
        X = self._align(X_behavior, self.behavior_feature_columns)
        raw = -np.asarray(self.behavior_model.decision_function(X), dtype=float)
        return np.clip((raw - self.norm_lo) / (self.norm_hi - self.norm_lo), 0.0, 1.0)

    @staticmethod
    def _align(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
        """按模型期望的列顺序对齐（缺失列补 0，保留行为但记录告警）."""
        missing = [c for c in columns if c not in df.columns]
        if missing:
            logger.warning(
                "特征对齐缺失关键特征列，已补 0（%d 列）| missing=%s",
                len(missing),
                missing,
            )
        return df.reindex(columns=columns, fill_value=0.0)

    def fuse(self, X_struct: pd.DataFrame, X_behavior: pd.DataFrame) -> tuple[np.ndarray, dict]:
        """返回 (score_final, modality_scores_dict)."""
        s_struct = self.score_struct(X_struct)
        s_behav = self.score_behavior(X_behavior)
        score_final = WEIGHT_STRUCTURED * s_struct + WEIGHT_BEHAVIOR * s_behav
        return score_final, {"structured": s_struct, "behavior": s_behav}

    # ----- 单/批量预测 -----
    def predict(self, structured_features: pd.DataFrame, behavior_features: pd.DataFrame) -> dict:
        """单员工预测，返回 {risk_score, risk_level, modality_scores}."""
        score_final, modality = self.fuse(structured_features, behavior_features)
        sf = float(score_final[0])
        risk_score = round(sf * 100)
        return {
            "risk_score": risk_score,
            "risk_level": score_to_level(risk_score),
            "modality_scores": {
                "structured": float(modality["structured"][0]),
                "behavior": float(modality["behavior"][0]),
            },
        }

    def predict_batch(self, structured_features: pd.DataFrame, behavior_features: pd.DataFrame) -> list[dict]:
        """批量预测，返回每行一个 dict."""
        score_final, modality = self.fuse(structured_features, behavior_features)
        results = []
        for i, sf in enumerate(score_final):
            risk_score = round(sf * 100)
            results.append({
                "risk_score": risk_score,
                "risk_level": score_to_level(risk_score),
                "score_final": float(sf),
                "score_structured": float(modality["structured"][i]),
                "score_behavior": float(modality["behavior"][i]),
            })
        return results


def evaluate() -> dict:
    """在测试集上评估融合模型，保存指标与预测明细."""
    data = load_split()
    X_struct_test = data["X_struct_test"]
    X_behavior_test = data["X_behav_test"]
    y_test = data["y_test"].to_numpy()
    audit_test = data["audit_test"].reset_index(drop=True)

    engine = FusionEngine()
    score_final, modality = engine.fuse(X_struct_test, X_behavior_test)

    auc = roc_auc_score(y_test, score_final)
    risk_scores = np.round(score_final * 100).astype(int)

    # 高风险前 20% 召回率
    n = len(y_test)
    top_k = max(1, int(np.ceil(n * 0.20)))
    order = np.argsort(-score_final)  # 降序
    top_idx = order[:top_k]
    total_pos = int(y_test.sum())
    pos_in_top = int(y_test[top_idx].sum())
    recall_at_top20 = pos_in_top / total_pos if total_pos > 0 else 0.0
    precision_at_top20 = pos_in_top / top_k

    # 二分类报告（阈值 0.5）
    y_pred = (score_final >= 0.5).astype(int)
    report = classification_report(y_test, y_pred, output_dict=True, digits=4, zero_division=0)

    # 保存预测明细（含审计字段，供公平性测试用）
    pred_df = pd.DataFrame({
        "score_final": score_final,
        "risk_score": risk_scores,
        "predicted_high_risk": (risk_scores >= 60).astype(int),
        "actual": y_test,
        "score_structured": modality["structured"],
        "score_behavior": modality["behavior"],
    })
    pred_df = pd.concat([pred_df, audit_test], axis=1)
    pred_df.to_csv(TEST_PREDICTIONS_PATH, index=False)

    metrics = {
        "auc_test": float(auc),
        "recall_at_top20": float(recall_at_top20),
        "precision_at_top20": float(precision_at_top20),
        "top20_size": top_k,
        "total_attrition": total_pos,
        "attrition_in_top20": pos_in_top,
        "classification_report": report,
        "weights": {"structured": WEIGHT_STRUCTURED, "behavior": WEIGHT_BEHAVIOR},
        "targets": {"auc_min": 0.85, "recall_top20_min": 0.80},
        "passed": bool(auc >= 0.85 and recall_at_top20 >= 0.80),
    }
    with open(FUSION_METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print("[T-306] 融合模型测试集 AUC = {:.4f}（目标 ≥ 0.85，{}）".format(
        auc, "达标" if auc >= 0.85 else "未达标"))
    print("[T-306] 前 20% 召回率 = {:.4f}（目标 ≥ 0.80，{}）".format(
        recall_at_top20, "达标" if recall_at_top20 >= 0.80 else "未达标"))
    print(f"[T-306] 前 20%（{top_k} 人）中捕获离职 {pos_in_top}/{total_pos}")
    print(f"[T-306] 预测明细保存到 {TEST_PREDICTIONS_PATH}")
    print(f"[T-306] 融合指标保存到 {FUSION_METRICS_PATH}")
    return metrics


if __name__ == "__main__":
    evaluate()
