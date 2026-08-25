"""风险预测路由（D05 3.3 + 3.10 全局解释 + 3.3 SHAP 解释 + 行为事件扩容）."""
import csv
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.logging import get_logger
from app.core.tenant import get_current_tenant_id
from app.db.session import get_db
from app.models.risk_prediction import RiskPrediction
from app.models.user import ROLE_ADMIN, ROLE_HR_MANAGER, ROLE_HRBP, ROLE_MANAGER, User
from app.schemas.risk import (
    GlobalExplanationOut,
    PredictRequest,
    RiskPredictionOut,
    ShapExplanationOut,
    ShapFactor,
)
from app.services.behavior_service import (
    EVENT_REPORT_EXPORTED,
    EVENT_RISK_PREDICTION_VIEWED,
    record_behavior_event,
    resolve_employee_id_by_email,
)
from app.services.risk_service import RiskService, get_feature_display_name

router = APIRouter()
logger = get_logger(__name__)

# 风险预测角色：HR 经理 / HRBP / 管理员 / 直线经理
_RISK_ROLES = (ROLE_ADMIN, ROLE_HR_MANAGER, ROLE_HRBP, ROLE_MANAGER)
# 报表导出角色（FR-EMP-005：数据导出仅 HR 经理 + 管理员）
_EXPORT_ROLES = (ROLE_ADMIN, ROLE_HR_MANAGER)

# 导出上限（防全表加载）
_EXPORT_MAX_ROWS = 10000


async def _record_prediction_viewed(
    db: AsyncSession,
    tenant_id: UUID,
    employee_id: UUID,
    *,
    prediction_id: UUID,
    risk_score: int,
    cached: bool,
) -> None:
    """风险预测查看行为事件（best-effort，内部失败降级跳过）."""
    await record_behavior_event(
        db=db,
        tenant_id=tenant_id,
        employee_id=employee_id,
        event_type=EVENT_RISK_PREDICTION_VIEWED,
        payload={
            "prediction_id": str(prediction_id),
            "risk_score": risk_score,
            "cached": cached,
        },
    )


@router.get("/employees/{employee_id}", response_model=RiskPredictionOut)
async def get_employee_risk(
    employee_id: UUID,
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_RISK_ROLES)),
):
    """获取员工风险预测（D05 3.3 GET /risk/employees/{id}）.

    行为特征基建：查看预测记 risk_prediction_viewed 行为事件（best-effort）。
    """
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

    await _record_prediction_viewed(
        db, tenant_id, employee_id,
        prediction_id=pred_id,
        risk_score=result["risk_score"],
        cached=bool(result["cached"]),
    )

    return RiskPredictionOut(
        prediction_id=pred_id,
        employee_id=employee_id,
        risk_score=result["risk_score"],
        risk_level=result["risk_level"],
        modality_scores=result["modality_scores"],
        model_version=result["model_version"],
        predicted_at=result["predicted_at"],
        cached=result["cached"],
        behavior_data_source=result.get("behavior_data_source"),
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
        behavior_data_source=result.get("behavior_data_source"),
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


@router.post("/reports/export")
async def export_risk_report(
    window_days: int = Query(30, ge=1, le=365),
    file_format: Literal["csv"] = Query("csv", alias="format"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(*_EXPORT_ROLES)),
):
    """导出风险预测报表 CSV（D05 POST /reports/export 轻量实现，仅 admin/hr_manager）.

    近 window_days 天本租户 risk_predictions 流水（脱敏：不含 feature_values 明细）。
    行为特征基建：导出记 report_exported 行为事件——按登录账号 email best-effort
    匹配员工，未匹配则跳过（与 login 事件接线口径一致，HR 管理账号不污染行为特征）。
    """
    tenant_id = get_current_tenant_id()
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    stmt = (
        select(RiskPrediction)
        .where(
            RiskPrediction.tenant_id == tenant_id,
            RiskPrediction.predicted_at >= cutoff,
        )
        .order_by(RiskPrediction.predicted_at.desc())
        .limit(_EXPORT_MAX_ROWS)
    )
    rows = (await db.execute(stmt)).scalars().all()

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "prediction_id", "employee_id", "model_version",
        "risk_score", "risk_level", "predicted_at",
    ])
    for r in rows:
        writer.writerow([
            str(r.id), str(r.employee_id), r.model_version,
            r.risk_score, r.risk_level, r.predicted_at.isoformat(),
        ])

    # 行为事件（best-effort）：report_exported
    try:
        employee_id = await resolve_employee_id_by_email(db, tenant_id, user.email)
        if employee_id is not None:
            await record_behavior_event(
                db=db,
                tenant_id=tenant_id,
                employee_id=employee_id,
                event_type=EVENT_REPORT_EXPORTED,
                payload={
                    "window_days": window_days,
                    "row_count": len(rows),
                    "format": file_format,
                },
            )
        else:
            logger.debug("报表导出事件跳过：登录账号无匹配员工 | user_id=%s", user.id)
    except Exception as e:  # noqa: BLE001
        logger.warning("报表导出行为事件记录失败（降级跳过） | err=%s", e)

    filename = f"risk_report_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
