"""T-307 SHAP 解释器 - 对 LightGBM 结构化模型做归因.

- 用 shap.TreeExplainer 计算 SHAP 值。
- explain(features) 返回 Top3（按 |SHAP|）特征贡献，标注方向：
    shap_value > 0 → positive（推高离职风险）
    shap_value < 0 → negative（降低风险）
- 产物：models/shap_example.json（一条测试样本的解释示例）。
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import shap

from app.ml.feature_engineering import load_split

MODELS_DIR = Path(__file__).resolve().parent / "models"
STRUCT_MODEL_PATH = MODELS_DIR / "structured_lgbm.pkl"
SHAP_EXAMPLE_PATH = MODELS_DIR / "shap_example.json"


class ShapExplainer:
    """LightGBM 的 SHAP 解释器."""

    def __init__(self) -> None:
        artifact = joblib.load(STRUCT_MODEL_PATH)
        self.model = artifact["model"]
        self.feature_columns = artifact["feature_columns"]
        # TreeExplainer 接受 lgb.Booster
        self.explainer = shap.TreeExplainer(self.model)

    def _to_array(self, shap_values) -> np.ndarray:
        """兼容 shap 不同版本：二分类可能返回 list 或 array，统一取正类."""
        if isinstance(shap_values, list):
            # 取正类（最后一个元素）
            arr = np.asarray(shap_values[-1])
        else:
            arr = np.asarray(shap_values)
        # 形如 (n, features, 2) 时取正类
        if arr.ndim == 3:
            arr = arr[:, :, -1]
        return arr

    def explain(self, features: pd.DataFrame, top_k: int = 3) -> list[dict]:
        """解释单条样本，返回 TopK 特征贡献.

        Args:
            features: 单行特征 DataFrame（列须与模型特征列一致）。
            top_k: 返回前 K 个贡献。
        """
        X = features.reindex(columns=self.feature_columns, fill_value=0.0)
        sv = self.explainer.shap_values(X)
        sv = self._to_array(sv)
        row = sv[0]  # 单行
        contributions = [
            {"feature": f, "contribution": float(v), "direction": "positive" if v > 0 else "negative"}
            for f, v in zip(self.feature_columns, row)
        ]
        # 按 |contribution| 降序取 TopK
        contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)
        return contributions[:top_k]


def generate_example() -> dict:
    """对测试集首条样本生成 SHAP 解释示例并保存."""
    data = load_split()
    X_test = data["X_struct_test"]
    y_test = data["y_test"]
    explainer = ShapExplainer()

    # 取一条高风险样本作为示例（更有解释意义）
    proba = explainer.model.predict(X_test)
    high_idx = int(np.argmax(proba))
    sample = X_test.iloc[[high_idx]]

    top3 = explainer.explain(sample)
    example = {
        "sample_index": int(high_idx),
        "actual_label": int(y_test.iloc[high_idx]),
        "predicted_score": float(proba[high_idx]),
        "feature_values": {k: (None if pd.isna(v) else (
            float(v) if isinstance(v, (np.floating, float)) else (
                int(v) if isinstance(v, (np.integer, int)) else str(v))))
            for k, v in sample.iloc[0].to_dict().items()},
        "top3_contributions": top3,
    }
    with open(SHAP_EXAMPLE_PATH, "w", encoding="utf-8") as f:
        json.dump(example, f, ensure_ascii=False, indent=2)

    print(f"[T-307] SHAP 示例（样本 #{high_idx}，预测分 {proba[high_idx]:.3f}）：")
    for c in top3:
        print(f"       {c['feature']:<28} contribution={c['contribution']:+.4f}  [{c['direction']}]")
    print(f"[T-307] 示例保存到 {SHAP_EXAMPLE_PATH}")
    return example


if __name__ == "__main__":
    generate_example()
