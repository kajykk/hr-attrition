"""T-308 公平性测试 - 4 维度 demographic parity.

维度：gender(M/F)、age(<35 vs >=35)、ethnicity(0=汉族/1=少数民族)、disability(0/1)。
指标：demographic parity difference = 各组"高风险预测"率之差的最大绝对值。
高风险预测定义：risk_score >= 60。

目标：4 个维度偏差均 < 5%（0.05）。
若某维度 ≥ 5%：打印警告并尝试阈值调整缓解（在 50-70 间搜索最优先行阈值）。
产物：models/fairness_report.json。

公平性硬约束已在数据生成与特征工程阶段保证：标签与模型特征均不含
gender/ethnicity/disability/birth_date；本测试仅为审计与兜底。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

MODELS_DIR = Path(__file__).resolve().parent / "models"
TEST_PREDICTIONS_PATH = MODELS_DIR / "test_predictions.csv"
FAIRNESS_REPORT_PATH = MODELS_DIR / "fairness_report.json"

DEFAULT_THRESHOLD = 60
THRESHOLD_GRID = list(range(50, 71))  # 缓解阈值搜索范围
PARITY_MAX = 0.05


def _group_parity(df: pd.DataFrame, col: str, threshold: int) -> dict:
    """计算单维度各组高风险预测率与最大组间差异."""
    high = (df["risk_score"] >= threshold).astype(int)
    tmp = df.copy()
    tmp["__high__"] = high
    rates = tmp.groupby(col)["__high__"].mean().to_dict()
    vals = list(rates.values())
    diff = float(max(vals) - min(vals)) if len(vals) >= 2 else 0.0
    return {
        "groups": {str(k): float(v) for k, v in rates.items()},
        "parity_difference": diff,
        "passed": bool(diff < PARITY_MAX),
    }


def _build_dimensions(df: pd.DataFrame) -> dict:
    """构造 4 个公平性维度的分组列."""
    dim = {}
    # gender: M/F
    dim["gender"] = {"col": "gender", "label": "性别 (M/F)"}
    # age: <35 vs >=35
    age_group = np.where(df["age_derived"] < 35, "<35", ">=35")
    df = df.copy()
    df["age_group"] = age_group
    dim["age"] = {"col": "age_group", "label": "年龄 (<35 / >=35)"}
    # ethnicity: 0=汉族 / 1=少数民族
    df["ethnicity_label"] = df["ethnicity"].map({0: "汉族", 1: "少数民族"}).astype(str)
    dim["ethnicity"] = {"col": "ethnicity_label", "label": "民族 (汉族/少数民族)"}
    # disability: 0/1
    df["disability_label"] = df["disability"].map({0: "无障碍", 1: "有障碍"}).astype(str)
    dim["disability"] = {"col": "disability_label", "label": "残障 (0/1)"}
    return dim, df


def _evaluate_at_threshold(df: pd.DataFrame, threshold: int) -> tuple[dict, float]:
    """在指定阈值下评估 4 维度，返回 (per_dim_report, max_parity)."""
    dims, work = _build_dimensions(df)
    report = {}
    max_parity = 0.0
    for name, info in dims.items():
        res = _group_parity(work, info["col"], threshold)
        res["label"] = info["label"]
        report[name] = res
        max_parity = max(max_parity, res["parity_difference"])
    return report, max_parity


def run_fairness_test() -> dict:
    """执行公平性测试，必要时尝试阈值调整缓解."""
    df = pd.read_csv(TEST_PREDICTIONS_PATH)

    # 默认阈值 60 评估
    report_at_default, max_parity_default = _evaluate_at_threshold(df, DEFAULT_THRESHOLD)

    mitigated = False
    final_threshold = DEFAULT_THRESHOLD
    final_report = report_at_default
    final_max_parity = max_parity_default

    if max_parity_default >= PARITY_MAX:
        print(f"[T-308] 警告：默认阈值 {DEFAULT_THRESHOLD} 下最大偏差 "
              f"{max_parity_default:.4f} ≥ {PARITY_MAX}，尝试阈值调整缓解 ...")
        # 在 50-70 间搜索使最大偏差最小的阈值（偏向 60）
        best = None
        for t in THRESHOLD_GRID:
            rep, mp = _evaluate_at_threshold(df, t)
            cand = (mp, abs(t - DEFAULT_THRESHOLD), t, rep)
            if best is None or cand[0] < best[0] or (
                cand[0] == best[0] and cand[1] < best[1]
            ):
                best = cand
        final_threshold = best[2]
        final_report = best[3]
        final_max_parity = best[0]
        mitigated = True
        print(f"[T-308] 缓解后选用阈值 {final_threshold}，最大偏差 {final_max_parity:.4f}")

    overall_passed = bool(final_max_parity < PARITY_MAX)

    result = {
        "default_threshold": DEFAULT_THRESHOLD,
        "final_threshold": final_threshold,
        "mitigation_applied": mitigated,
        "dimensions": final_report,
        "max_parity_difference": final_max_parity,
        "target_max_parity": PARITY_MAX,
        "overall_passed": overall_passed,
    }
    with open(FAIRNESS_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[T-308] 公平性测试（阈值 {final_threshold}，缓解={mitigated}）：")
    for name, info in final_report.items():
        flag = "达标" if info["passed"] else "未达标"
        print(f"       {info['label']:<22} 偏差={info['parity_difference']:.4f}  [{flag}]")
    print(f"[T-308] 最大偏差 {final_max_parity:.4f}（目标 < {PARITY_MAX}，"
          f"{'达标' if overall_passed else '未达标'}）")
    print(f"[T-308] 报告保存到 {FAIRNESS_REPORT_PATH}")
    return result


if __name__ == "__main__":
    run_fairness_test()
