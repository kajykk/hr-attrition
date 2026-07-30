"""LLM 建议路由 - SSE 流式（D05 3.5 POST /advise/stream + D03 4.4）."""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import decrypt_pii
from app.core.tenant import get_current_tenant_id
from app.db.session import get_db
from app.models.employee import Employee
from app.models.user import User
from app.models.warning import WarningRecord
from app.schemas.risk import ShapFactor
from app.services.llm_service import LLMService, sanitize_pii
from app.services.risk_service import RiskService, get_feature_display_name

router = APIRouter()


async def _load_employee_for_advise(
    db: AsyncSession, tenant_id: str, warning_id: UUID
) -> tuple[WarningRecord, Employee]:
    """加载预警关联员工（含 PII，将在 LLM 调用前脱敏）."""
    stmt = select(WarningRecord).where(
        WarningRecord.id == warning_id,
        WarningRecord.tenant_id == tenant_id,
    )
    w = (await db.execute(stmt)).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="预警不存在")

    stmt2 = select(Employee).where(
        Employee.id == w.employee_id,
        Employee.tenant_id == tenant_id,
    )
    emp = (await db.execute(stmt2)).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="员工不存在")
    return w, emp


async def _fetch_real_shap_factors(
    db: AsyncSession, tenant_id: str, employee: Employee, fallback_score: int
) -> tuple[list[dict], int]:
    """从 RiskService 获取真实 SHAP factors（W5 改用真实 SHAP，D03 4.4）.

    调用 RiskService.predict(force_refresh=True) 获取最新预测 + shap_factors。
    若 RiskService 未返回 shap_factors（模型未加载等），返回空列表（由调用方降级占位）。

    Returns:
        (shap_factors, risk_score)：SHAP 因子列表 + 实际风险分
    """
    try:
        result = await RiskService.predict(
            employee_id=employee.id,
            tenant_id=tenant_id,
            force_refresh=True,
            db=db,
        )
        shap_factors = result.get("shap_factors") or []
        risk_score = int(result.get("risk_score", fallback_score))
        # 补充 display_name（SHAP 返回的 dict 可能只有 feature/contribution/direction）
        enriched: list[dict] = []
        for f in shap_factors:
            feat = f.get("feature", "")
            item = dict(f)
            item.setdefault("display_name", get_feature_display_name(feat))
            enriched.append(item)
        return enriched, risk_score
    except Exception:  # noqa: BLE001
        # RiskService 调用失败（如 Kill Switch 激活、模型异常）→ 降级用占位
        return [], fallback_score


def _placeholder_shap_factors(employee: Employee) -> list[dict]:
    """占位 SHAP 因子（RiskService 不可用或未返回 shap_factors 时降级）."""
    return [
        {"feature": "salary_percentile", "display_name": "薪资分位",
         "value": float(employee.salary_percentile) if employee.salary_percentile else None,
         "contribution": -0.15, "direction": "negative"},
        {"feature": "promotion_gap_months", "display_name": "晋升间隔",
         "value": 28, "contribution": 0.12, "direction": "positive"},
    ]


@router.post("/stream")
async def advise_stream(
    warning_id: UUID,
    prediction_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """LLM 保留建议 SSE 流式生成（D05 3.5 + D03 4.4 + ADR-003）.

    流程：
      1. 加载员工档案（含 PII）
      2. PII 脱敏（sanitize_pii）：姓名→员工A，身份证/手机/薪资移除
      3. 调 RiskService.predict 获取真实 SHAP factors（W5 改造）；不可用时降级占位
      4. 调用通义千问 Max（DashScope SSE），失败回退 DeepSeek-V3，再失败降级规则模板
      5. SSE 推送：data: {"chunk": "..."} → data: {"metadata": {...}} → data: [DONE]
    """
    tenant_id = get_current_tenant_id()
    warning, emp = await _load_employee_for_advise(db, tenant_id, warning_id)

    # 构造员工 dict（含解密后的明文 PII，下一步立即脱敏）
    name_plain = decrypt_pii(emp.name_encrypted) or ""
    employee_dict = {
        "name": name_plain,
        "department_name": None,  # 实际可关联 Department 取 name
        "position": emp.position,
        "level": emp.level,
        # 以下 PII 字段将在 sanitize_pii 中移除
        "id_card": decrypt_pii(emp.id_card_encrypted),
        "phone": decrypt_pii(emp.phone_encrypted),
        "salary": decrypt_pii(emp.salary_encrypted),
        "ethnicity": decrypt_pii(emp.ethnicity_encrypted),
        "disability": decrypt_pii(emp.disability_encrypted),
    }
    # PII 脱敏（D03 4.4 + D04 7.2）
    sanitized = sanitize_pii(employee_dict)

    # W5 改造：从 RiskService 获取真实 SHAP factors
    real_shap, real_score = await _fetch_real_shap_factors(db, tenant_id, emp, warning.risk_score)
    if real_shap:
        shap_factors = real_shap
        risk_score = real_score
    else:
        # 降级：RiskService 未返回 shap_factors，用占位（保持向后兼容）
        shap_factors = _placeholder_shap_factors(emp)
        risk_score = warning.risk_score

    async def _sse():
        async for chunk in LLMService.stream_advice(
            sanitized, shap_factors, risk_score
        ):
            if "chunk" in chunk:
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            elif "metadata" in chunk:
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            elif chunk.get("done"):
                yield "data: [DONE]\n\n"
                return

    return StreamingResponse(_sse(), media_type="text/event-stream")
