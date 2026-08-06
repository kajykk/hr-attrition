"""风险预测路由（D05 3.3 + 3.10 全局解释 + 3.3 SHAP 解释）."""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.tenant import get_current_tenant_id
from app.db.session import get_db
from app.models.user import ROLE_ADMIN, ROLE_HR_MANAGER, ROLE_HRBP, ROLE_MANAGER, User
from app.schemas.risk import (
    GlobalExplanationOut,
    PredictRequest,
    RiskPredictionOut,
    ShapExplanationOut,
    ShapFactor,
)
from app.services.risk_service import RiskService, get_feature_display_name

router = APIRouter()

# 风险预测角色：HR 经理 / HRBP / 管理员 / 直线经理
_RISK_ROLES = (ROLE_ADMIN, ROLE_HR_MANAGER, ROLE_HRBP, ROLE_MANAGER)


@router.get("/employees/{employee_id}", response_model=RiskPredictionOut)
async def get_employee_risk(
    employee_id: UUID,
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_RISK_ROLES)),
):
    """获取员工风险预测（D05 3.3 GET /risk/employees/{id}）."""
    tenant_id = get_current_tenant_id()
    result = await RiskService.predict(
        employee_id, tenant_id, force_refresh=force_refresh, db=db
    )
    # prediction_id 可能是字符串（来自缓存）或 UUID
    pred_id = result.get("prediction_id") or str(employee_id)
    if isinstance(pred_id, str):
        try:
            pred_id = UUID(pred_id)
        except (ValueError, AttributeError):
            pred_id = employee_id
    return RiskPredictionOut(
        prediction_id=pred_id,
        employee_id=employee_id,
        risk_score=result["risk_score"],
        risk_level=result["risk_level"],
        modality_scores=result["modality_scores"],
        model_version=result["model_version"],
        predicted_at=result["predicted_at"],
        cached=result["cached"],
    )


@router.post("/predict", response_model=RiskPredictionOut)
async def predict_risk(
    payload: PredictRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_RISK_ROLES)),
):
    """单员工风险预测（D05 3.3 POST /risk/predict）."""
    tenant_id = get_current_tenant_id()
    result = await RiskService.predict(
        payload.employee_id, tenant_id, force_refresh=payload.force_refresh, db=db
    )
    pred_id = result.get("prediction_id") or str(payload.employee_id)
    if isinstance(pred_id, str):
        try:
            pred_id = UUID(pred_id)
        except (ValueError, AttributeError):
            pred_id = payload.employee_id
    return RiskPredictionOut(
        prediction_id=pred_id,
        employee_id=payload.employee_id,
        risk_score=result["risk_score"],
        risk_level=result["risk_level"],
        modality_scores=result["modality_scores"],
        model_version=result["model_version"],
        predicted_at=result["predicted_at"],
        cached=result["cached"],
    )


@router.get("/employees/{employee_id}/explanation", response_model=ShapExplanationOut)
async def get_employee_explanation(
    employee_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_RISK_ROLES)),
):
    """获取员工 SHAP 解释（D05 3.3 GET /risk/employees/{id}/explanation）.

    返回 Top3 特征贡献（按 |SHAP| 降序），含中文 display_name。
    """
    tenant_id = get_current_tenant_id()
    # 强制刷新以获取最新 SHAP（不走缓存）
    result = await RiskService.predict(
        employee_id, tenant_id, force_refresh=True, db=db
    )

    shap_factors = result.get("shap_factors") or []
    if not shap_factors:
        # SHAP 不可用（模型未加载）→ 返回空 factors 但仍需 prediction_id
        factors = []
    else:
        factors = [
            ShapFactor(
                feature=f["feature"],
                display_name=get_feature_display_name(f["feature"]),
                contribution=f["contribution"],
                direction=f["direction"],
            )
            for f in shap_factors
        ]

    pred_id = result.get("prediction_id") or str(employee_id)
    if isinstance(pred_id, str):
        try:
            pred_id = UUID(pred_id)
        except (ValueError, AttributeError):
            pred_id = employee_id

    # base_value 用 0.0 占位（TreeExplainer 的期望值，简化处理）
    # output_value = risk_score / 100（归一化到 0-1）
    return ShapExplanationOut(
        prediction_id=pred_id,
        factors=factors,
        base_value=0.0,
        output_value=float(result["risk_score"]) / 100.0,
        computed_at=datetime.now(UTC),
    )


@router.get("/global-explanation", response_model=GlobalExplanationOut)
async def global_explanation(
    window_days: int = 30,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_RISK_ROLES)),
):
    """全局特征重要性（D05 3.10 GET /risk/global-explanation，近 30 天聚合）."""
    tenant_id = get_current_tenant_id()
    result = await RiskService.global_explanation(
        tenant_id, window_days=window_days, db=db
    )
    return GlobalExplanationOut(
        model_version=result["model_version"],
        window_days=result["window_days"],
        top_features=[ShapFactor(**f) for f in result["top_features"]],
        computed_at=result["computed_at"],
    )
