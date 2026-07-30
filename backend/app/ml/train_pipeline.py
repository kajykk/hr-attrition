"""T-301~T-308 一键训练入口.

顺序执行：数据生成 → 特征工程 → 结构化模型 → 行为模型 → 融合引擎 → SHAP 解释 → 公平性测试。
最后打印汇总报告，并按达标情况返回退出码（0=全部达标，1=存在未达标项）。

运行：.venv\\Scripts\\python.exe -m app.ml.train_pipeline
"""
from __future__ import annotations

import sys
import warnings

# 屏蔽 SDV / shap 等库的 UserWarning 噪声（不影响逻辑）
warnings.filterwarnings("ignore", category=UserWarning)

from app.ml import data_generation, feature_engineering, train_structured, train_behavior
from app.ml import fusion_engine, shap_explainer, fairness_test


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run_pipeline() -> dict:
    """顺序执行全流程，返回汇总指标 dict."""
    summary: dict = {}

    # 1. 数据生成
    _banner("[1/7] T-301 数据生成")
    structured, behavior = data_generation.generate_all()
    summary["attrition_rate"] = float(structured["Attrition"].eq("Yes").mean())
    summary["n_rows"] = len(structured)

    # 2. 特征工程
    _banner("[2/7] T-302 特征工程")
    feature_engineering.build_features()

    # 3. 结构化模型
    _banner("[3/7] T-303 LightGBM 结构化模型")
    struct_metrics = train_structured.train()
    summary["structured_auc"] = struct_metrics["auc_test"]
    summary["structured_passed"] = struct_metrics["passed"]

    # 4. 行为模型
    _banner("[4/7] T-305 IsolationForest 行为模型")
    behavior_metrics = train_behavior.train()
    summary["behavior_auc"] = behavior_metrics["auc_test"]
    summary["behavior_passed"] = behavior_metrics["passed"]

    # 5. 融合引擎
    _banner("[5/7] T-306 多模态融合引擎")
    fusion_metrics = fusion_engine.evaluate()
    summary["fusion_auc"] = fusion_metrics["auc_test"]
    summary["recall_at_top20"] = fusion_metrics["recall_at_top20"]
    summary["fusion_passed"] = fusion_metrics["passed"]

    # 6. SHAP 解释
    _banner("[6/7] T-307 SHAP 解释")
    shap_explainer.generate_example()
    summary["shap_example_generated"] = True

    # 7. 公平性测试
    _banner("[7/7] T-308 公平性测试")
    fairness_result = fairness_test.run_fairness_test()
    summary["fairness_max_parity"] = fairness_result["max_parity_difference"]
    summary["fairness_per_dim"] = {
        name: info["parity_difference"] for name, info in fairness_result["dimensions"].items()
    }
    summary["fairness_passed"] = fairness_result["overall_passed"]

    # 汇总达标判定
    summary["all_passed"] = bool(
        summary["structured_passed"]
        and summary["behavior_passed"]
        and summary["fusion_passed"]
        and summary["fairness_passed"]
    )
    return summary


def print_summary(summary: dict) -> None:
    _banner("W3 机器学习流水线 - 汇总报告")
    print(f"数据规模：{summary['n_rows']} 行，离职率 {summary['attrition_rate']:.2%}")
    print("-" * 72)

    def _line(label: str, value: float, target: str, passed: bool) -> None:
        flag = "达标" if passed else "未达标"
        print(f"  {label:<28} {value:.4f}   目标 {target}   [{flag}]")

    _line("结构化模型 AUC", summary["structured_auc"], "≥ 0.85", summary["structured_passed"])
    _line("行为模型 AUC", summary["behavior_auc"], "≥ 0.65", summary["behavior_passed"])
    _line("融合模型 AUC", summary["fusion_auc"], "≥ 0.85", summary["fusion_passed"])
    _line("前 20% 离职召回率", summary["recall_at_top20"], "≥ 0.80",
          summary["recall_at_top20"] >= 0.80)
    _line("公平性最大偏差", summary["fairness_max_parity"], "< 0.05", summary["fairness_passed"])

    print("-" * 72)
    print("  公平性 4 维度明细：")
    for name, diff in summary["fairness_per_dim"].items():
        print(f"    {name:<12} 偏差 {diff:.4f}  [{'达标' if diff < 0.05 else '未达标'}]")

    print("-" * 72)
    verdict = "全部达标" if summary["all_passed"] else "存在未达标项"
    print(f"  总体结论：{verdict}")
    print("=" * 72)


if __name__ == "__main__":
    result = run_pipeline()
    print_summary(result)
    sys.exit(0 if result["all_passed"] else 1)
