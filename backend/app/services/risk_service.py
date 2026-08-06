"""RiskService - 风险预测服务（W4 集成 FusionEngine + ShapExplainer + Redis 缓存）.

职责（参考 D03 4.1 + D04 6.2）：
  1. predict(employee_id, tenant_id) → 调 FusionEngine 预测，写 risk_predictions 表
  2. Redis 缓存 risk:{tenant_id}:{employee_id}（TTL 3600s，失败降级无缓存）
  3. 同步触发 ShapExplainer.eplain（D03 ADR-004 解耦后续优化）
  4. global_explanation → 聚合近 30 天 feature_values 计算 Top10 特征平均 |SHAP|

降级策略：
  - Redis 不可用 → 跳过缓存
  - FusionEngine/ShapExplainer 加载失败（无模型文件）→ 返回占位结果，不崩溃
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.employee import Employee
from app.models.risk_prediction import (
    RISK_LEVEL_HIGH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    RISK_LEVEL_MEDIUM_HIGH,
    RISK_LEVEL_MEDIUM_LOW,
    RiskPrediction,
)

logger = get_logger(__name__)

# 模型版本（W4 起改为 fusion-engine-v1）
MODEL_VERSION = "fusion-engine-v1"

# Redis 缓存 key 模板与 TTL
_CACHE_KEY_TPL = "risk:{tenant_id}:{employee_id}"
_CACHE_TTL_SECONDS = 3600

# 全局解释聚合上限（防全表加载，超量取最近 N 条）
_GLOBAL_EXPLAIN_MAX_RECORDS = 20000


# ===== 模块级懒加载单例（避免每次请求加载模型） =====
_fusion_engine = None
_shap_explainer = None


def _aggregate_feature_contributions(
    feature_values_rows: list,
    default_top_features: list[dict],
) -> list[dict]:
    """同步聚合函数：从 feature_values 行计算 Top10 特征贡献度代理（标准差）.

    设计为普通同步函数，由 asyncio.to_thread 调用，避免在事件循环中做
    numpy 聚合（P1-7 优化）。direction 用均值相对中位数的偏移判断。
    """
    import numpy as np

    feat_values: dict[str, list[float]] = {}
    for fv in feature_values_rows:
        if not fv:
            continue
        for k, v in fv.items():
            try:
                feat_values.setdefault(k, []).append(float(v))
            except (TypeError, ValueError):
                continue

    contributions: list[dict] = []
    for feat, vals in feat_values.items():
        if len(vals) < 2:
            continue
        arr = np.array(vals)
        std = float(arr.std())
        mean = float(arr.mean())
        median = float(np.median(arr))
        # 方向：均值高于中位数 → positive，否则 negative
        direction = "positive" if mean > median else "negative"
        contributions.append({
            "feature": feat,
            "display_name": _FEATURE_DISPLAY_NAMES.get(feat, feat),
            "contribution": std,
            "direction": direction,
        })

    contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)
    return contributions[:10] if contributions else default_top_features


def _get_fusion_engine():
    """懒加载 FusionEngine 单例（首次 predict 时加载）.

    加载失败返回 None（测试环境可能无模型文件），调用方需处理。
    """
    global _fusion_engine
    if _fusion_engine is None:
        try:
            from app.ml.fusion_engine import FusionEngine
            _fusion_engine = FusionEngine()
            logger.info("FusionEngine 加载成功")
        except Exception as e:  # noqa: BLE001
            logger.error("FusionEngine 加载失败 | err=%s", e)
            _fusion_engine = None
    return _fusion_engine


def _get_shap_explainer():
    """懒加载 ShapExplainer 单例."""
    global _shap_explainer
    if _shap_explainer is None:
        try:
            from app.ml.shap_explainer import ShapExplainer
            _shap_explainer = ShapExplainer()
            logger.info("ShapExplainer 加载成功")
        except Exception as e:  # noqa: BLE001
            logger.error("ShapExplainer 加载失败 | err=%s", e)
            _shap_explainer = None
    return _shap_explainer


def _reset_singletons() -> None:
    """重置模块级单例（仅测试用）."""
    global _fusion_engine, _shap_explainer
    _fusion_engine = None
    _shap_explainer = None


class RiskService:
    """风险预测服务 - 对接 FusionEngine（W4 重写）."""

    MODEL_VERSION = MODEL_VERSION

    @staticmethod
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

    @classmethod
    async def predict(
        cls,
        employee_id: UUID,
        tenant_id: UUID,
        force_refresh: bool = False,
        db: AsyncSession | None = None,
    ) -> dict:
        """单员工风险预测.

        流程：
          0. 检查 Kill Switch（W5 新增）：激活则返回安全降级结果
          1. 检查 Redis 缓存，命中且非 force_refresh → 返回（cached=True）
          2. 从 DB 查 Employee（含 tenant_id 隔离）
          3. feature_provider.build_features(employee)
          4. FusionEngine.predict(structured, behavior)
          5. 写入 risk_predictions 表
          6. 同步触发 ShapExplainer.explain（D03 ADR-004）
          7. 写 Redis 缓存（TTL 3600s）
          8. 返回完整 dict

        Args:
            employee_id: 员工 ID
            tenant_id: 租户 ID
            force_refresh: 是否强制刷新（跳过缓存）
            db: 可选数据库会话（API 层传入；为 None 时不写库）

        Returns:
            dict: {prediction_id, employee_id, risk_score, risk_level,
                   modality_scores, model_version, predicted_at, cached, shap_factors}
        """
        # 0. Kill Switch 检查（W5 新增，D03 4.5 + PIPL/EU AI Act 合规）
        try:
            from app.core.kill_switch import is_active_async
            if await is_active_async():
                logger.warning(
                    "Kill Switch 已激活，返回安全降级预测 | employee_id=%s", employee_id
                )
                return {
                    "prediction_id": None,
                    "employee_id": str(employee_id),
                    "risk_score": 50,
                    "risk_level": RISK_LEVEL_MEDIUM,
                    "modality_scores": {"structured": 0.5, "behavior": 0.5},
                    "model_version": "kill-switch-active",
                    "predicted_at": datetime.now(UTC).isoformat(),
                    "cached": False,
                    "shap_factors": [],
                    "kill_switch": True,
                }
        except Exception as e:  # noqa: BLE001
            # Kill Switch 检查失败不阻塞主流程（fail-open）
            logger.warning("Kill Switch 检查异常，继续预测 | err=%s", e)

        cache_key = _CACHE_KEY_TPL.format(tenant_id=tenant_id, employee_id=employee_id)

        # 1. Redis 缓存查询（降级：Redis 不可用则跳过）
        redis = get_redis()
        if redis is not None and not force_refresh:
            try:
                cached_raw = await redis.get(cache_key)
                if cached_raw:
                    cached = json.loads(cached_raw)
                    cached["cached"] = True
                    logger.info("风险预测命中缓存 | employee_id=%s", employee_id)
                    return cached
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis 读取失败，跳过缓存 | err=%s", e)

        # 2. 从 DB 查 Employee（tenant_id 隔离）
        if db is None:
            logger.warning("RiskService.predict 无 db 会话，跳过员工查询 | employee_id=%s", employee_id)
            raise ValueError("predict 需要 db 会话以查询员工")

        stmt = select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == tenant_id,
            Employee.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        employee = result.scalar_one_or_none()
        if employee is None:
            raise ValueError(f"员工不存在或跨租户访问 | employee_id={employee_id}")

        # 3. 构造特征（先校验训练/推理特征契约，防漂移）
        from app.ml.feature_provider import assert_feature_contract, build_features
        assert_feature_contract()
        structured_df, behavior_df = build_features(employee)

        # 4. 调用 FusionEngine 预测（CPU 密集 → to_thread，避免阻塞事件循环；失败降级为占位）
        engine = _get_fusion_engine()
        if engine is None:
            logger.warning("FusionEngine 不可用，返回占位预测 | employee_id=%s", employee_id)
            risk_score = 50
            modality_scores = {"structured": 0.5, "behavior": 0.5}
        else:
            try:
                pred = await asyncio.to_thread(engine.predict, structured_df, behavior_df)
                risk_score = int(pred["risk_score"])
                modality_scores = pred["modality_scores"]
            except Exception as e:  # noqa: BLE001
                logger.error("FusionEngine.predict 异常 | employee_id=%s | err=%s", employee_id, e)
                risk_score = 50
                modality_scores = {"structured": 0.5, "behavior": 0.5}

        risk_level = cls.score_to_level(risk_score)
        now = datetime.now(UTC)

        # 5. 写入 risk_predictions 表（写失败直接抛出，由 API 层返回 500；不再静默吞掉）
        feature_values = {col: float(structured_df.iloc[0][col]) for col in structured_df.columns}
        record = RiskPrediction(
            tenant_id=tenant_id,
            employee_id=employee_id,
            model_version=cls.MODEL_VERSION,
            risk_score=risk_score,
            risk_level=risk_level,
            modality_scores=modality_scores,
            feature_values=feature_values,
            predicted_at=now,
        )
        db.add(record)
        await db.flush()
        prediction_id = record.id

        # 6. 同步触发 SHAP（CPU 密集 → to_thread；D03 ADR-004 解耦后续优化）
        shap_factors: list[dict] = []
        explainer = _get_shap_explainer()
        if explainer is not None:
            try:
                shap_factors = await asyncio.to_thread(explainer.explain, structured_df, top_k=3)
            except Exception as e:  # noqa: BLE001
                logger.warning("SHAP 计算失败 | employee_id=%s | err=%s", employee_id, e)

        # 构造返回 dict
        result_dict = {
            "prediction_id": str(prediction_id) if prediction_id else None,
            "employee_id": str(employee_id),
            "risk_score": risk_score,
            "risk_level": risk_level,
            "modality_scores": modality_scores,
            "model_version": cls.MODEL_VERSION,
            "predicted_at": now.isoformat(),
            "cached": False,
            "shap_factors": shap_factors,
        }

        # 7. 写 Redis 缓存（降级：失败仅 log warning）
        if redis is not None:
            try:
                await redis.set(cache_key, json.dumps(result_dict, ensure_ascii=False), ex=_CACHE_TTL_SECONDS)
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis 写入失败，跳过缓存 | err=%s", e)

        # 8. 异步推送 WebSocket（best-effort，失败不影响主流程）
        try:
            from app.api.v1.ws import broadcast_risk_update
            await broadcast_risk_update(
                tenant_id=str(tenant_id),
                message={
                    "type": "risk_update",
                    "employee_id": str(employee_id),
                    "risk_score": risk_score,
                    "risk_level": risk_level,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("WebSocket 广播失败（不影响主流程） | err=%s", e)

        return result_dict

    @classmethod
    async def global_explanation(
        cls,
        tenant_id: UUID,
        window_days: int = 30,
        db: AsyncSession | None = None,
    ) -> dict:
        """全局特征重要性（D05 3.10 GET /risk/global-explanation，近 30 天聚合）.

        从 risk_predictions 表聚合近 window_days 天的 feature_values，
        统计 Top10 特征的平均 |SHAP|（这里用特征值方差作为贡献度代理，因为 feature_values
        中未直接存 SHAP 值；生产环境可单独建 shap_values 表）。

        如果表无数据，返回默认占位（保持向后兼容）。

        Args:
            tenant_id: 租户 ID
            window_days: 时间窗口（天）
            db: 数据库会话
        """
        default_top_features = [
            {"feature": "salary_percentile", "display_name": "薪资分位",
             "contribution": -0.18, "direction": "negative"},
            {"feature": "promotion_gap_months", "display_name": "晋升间隔",
             "contribution": 0.14, "direction": "positive"},
            {"feature": "YearsSinceLastPromotion", "display_name": "上次晋升年限",
             "contribution": 0.12, "direction": "positive"},
        ]

        if db is None:
            # 无 DB 会话，返回默认占位
            return {
                "model_version": cls.MODEL_VERSION,
                "window_days": window_days,
                "top_features": default_top_features,
                "computed_at": datetime.now(UTC).isoformat(),
            }

        try:
            cutoff = datetime.now(UTC) - timedelta(days=window_days)
            # 只取 feature_values 列 + 限制聚合行数（避免全表加载大 JSONB）
            stmt = select(RiskPrediction.feature_values).where(
                RiskPrediction.tenant_id == tenant_id,
                RiskPrediction.predicted_at >= cutoff,
            ).order_by(
                RiskPrediction.predicted_at.desc()
            ).limit(_GLOBAL_EXPLAIN_MAX_RECORDS)
            rows = (await db.execute(stmt)).scalars().all()

            if not rows:
                return {
                    "model_version": cls.MODEL_VERSION,
                    "window_days": window_days,
                    "top_features": default_top_features,
                    "computed_at": datetime.now(UTC).isoformat(),
                }

            # CPU 密集聚合 → to_thread（避免阻塞事件循环）
            top_features = await asyncio.to_thread(
                _aggregate_feature_contributions,
                [r for r in rows],
                default_top_features,
            )

            return {
                "model_version": cls.MODEL_VERSION,
                "window_days": window_days,
                "top_features": top_features,
                "computed_at": datetime.now(UTC).isoformat(),
            }
        except Exception as e:  # noqa: BLE001
            logger.error("global_explanation 聚合失败，返回默认占位 | err=%s", e)
            return {
                "model_version": cls.MODEL_VERSION,
                "window_days": window_days,
                "top_features": default_top_features,
                "computed_at": datetime.now(UTC).isoformat(),
            }


# ===== 特征中文显示名映射（用于 SHAP 解释和全局解释） =====
_FEATURE_DISPLAY_NAMES: dict[str, str] = {
    "Age": "年龄",
    "DistanceFromHome": "通勤距离",
    "Education": "教育程度",
    "EnvironmentSatisfaction": "环境满意度",
    "JobInvolvement": "工作投入度",
    "JobLevel": "职级",
    "JobSatisfaction": "工作满意度",
    "MonthlyIncome": "月薪",
    "NumCompaniesWorked": "曾任职公司数",
    "PercentSalaryHike": "调薪幅度",
    "PerformanceRating": "绩效评级",
    "RelationshipSatisfaction": "关系满意度",
    "StockOptionLevel": "期权等级",
    "TotalWorkingYears": "总工作年限",
    "TrainingTimesLastYear": "年度培训次数",
    "WorkLifeBalance": "工作生活平衡",
    "YearsAtCompany": "在司年限",
    "YearsInCurrentRole": "当前岗位年限",
    "YearsSinceLastPromotion": "晋升间隔",
    "YearsWithCurrManager": "直属上级年限",
    "overtime_flag": "加班",
    "business_travel_ord": "出差频率",
    "dept_Sales": "销售部",
    "dept_RD": "研发部",
    "dept_HR": "人力资源部",
    "marital_Single": "未婚",
    "marital_Married": "已婚",
    "marital_Divorced": "离异",
    "salary_percentile": "薪资分位",
    "promotion_gap_months": "晋升间隔月数",
    "tenure_ratio": "任期比",
}


def get_feature_display_name(feature: str) -> str:
    """获取特征中文显示名."""
    return _FEATURE_DISPLAY_NAMES.get(feature, feature)
