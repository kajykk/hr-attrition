"""服务层测试 - audit_service / llm_service / risk_service 补充覆盖.

覆盖：
  - audit_service：append_audit_log 哈希链、verify_hash_chain 正常/篡改、_compute_hash 确定性
  - llm_service：sanitize_pii、build_prompt、_fallback_template、stream_advice 降级路径
  - risk_service：predict 完整流程、Redis 缓存命中、score_to_level 补全、global_explanation
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.audit_service import (
    GENESIS_HASH,
    _compute_hash,
    append_audit_log,
    verify_hash_chain,
)
from app.services.llm_service import (
    LLMService,
    _fallback_template,
    build_prompt,
    sanitize_pii,
)
from app.services.risk_service import RiskService, get_feature_display_name

# ============================================================
# 1. audit_service 测试
# ============================================================


def test_compute_hash_deterministic_same_input():
    """_compute_hash 对相同输入应返回相同哈希."""
    prev = GENESIS_HASH
    payload = {"action": "login", "user_id": "abc"}
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    h1 = _compute_hash(prev, payload, ts)
    h2 = _compute_hash(prev, payload, ts)
    assert h1 == h2
    # SHA256 hex 长度为 64
    assert len(h1) == 64


def test_compute_hash_changes_on_different_input():
    """不同输入应产生不同哈希."""
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    h1 = _compute_hash(GENESIS_HASH, {"action": "a"}, ts)
    h2 = _compute_hash(GENESIS_HASH, {"action": "b"}, ts)
    assert h1 != h2


def test_compute_hash_changes_on_different_prev():
    """不同 prev_hash 应产生不同 current_hash."""
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    payload = {"action": "login"}
    h1 = _compute_hash(GENESIS_HASH, payload, ts)
    h2 = _compute_hash("a" * 64, payload, ts)
    assert h1 != h2


def test_compute_hash_handles_non_serializable_via_default_str():
    """_compute_hash 用 default=str 处理非 JSON 序列化对象（如 UUID/datetime）."""
    uid = uuid4()
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    payload = {"tenant_id": uid, "ts": ts}
    # 不抛异常
    h = _compute_hash(GENESIS_HASH, payload, ts)
    assert len(h) == 64


@pytest.mark.asyncio
async def test_append_audit_log_first_record_uses_genesis_prev_hash():
    """首条审计日志 prev_hash 应为 GENESIS_HASH."""
    db = AsyncMock()
    # 无上一条记录
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    tenant_id = uuid4()
    log = await append_audit_log(
        db=db,
        tenant_id=tenant_id,
        action="create_employee",
        resource_type="employee",
        user_id=uuid4(),
        resource_id=uuid4(),
    )

    # 首条 prev_hash 应为 GENESIS_HASH
    assert log.prev_hash == GENESIS_HASH
    # current_hash 应为 64 位 hex
    assert len(log.current_hash) == 64
    assert log.current_hash != GENESIS_HASH
    # db.add 被调用
    db.add.assert_called_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_append_audit_log_second_record_chains_to_first():
    """第二条审计日志 prev_hash 应为第一条的 current_hash."""
    db = AsyncMock()

    # 构造第一条日志作为 last_log
    tenant_id = uuid4()
    first_log = MagicMock()
    first_log.current_hash = "a" * 64

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = first_log
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    log = await append_audit_log(
        db=db,
        tenant_id=tenant_id,
        action="update_employee",
        resource_type="employee",
    )

    # 第二条 prev_hash 应为第一条的 current_hash
    assert log.prev_hash == "a" * 64
    # current_hash 应不同于 prev_hash
    assert log.current_hash != log.prev_hash


@pytest.mark.asyncio
async def test_append_audit_log_with_optional_fields():
    """append_audit_log 应正确处理 before_value/after_value/ip/user_agent."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    log = await append_audit_log(
        db=db,
        tenant_id=uuid4(),
        action="kill_switch.activate",
        resource_type="kill_switch",
        before_value={"active": False},
        after_value={"active": True, "reason": "drift"},
        ip="192.168.1.1",
        user_agent="pytest/1.0",
    )

    assert log.action == "kill_switch.activate"
    assert log.resource_type == "kill_switch"
    assert log.before_value == {"active": False}
    assert log.after_value == {"active": True, "reason": "drift"}
    assert log.ip == "192.168.1.1"
    assert log.user_agent == "pytest/1.0"


@pytest.mark.asyncio
async def test_verify_hash_chain_intact_returns_true():
    """完整未篡改的哈希链应返回 True."""
    db = AsyncMock()

    # 构造 3 条日志，prev_hash 链式相接
    tenant_id = uuid4()
    now = datetime.now(UTC)

    # 第一条：prev=GENESIS, current=_compute_hash(GENESIS, ...)
    p1 = {
        "tenant_id": str(tenant_id),
        "user_id": None,
        "action": "a1",
        "resource_type": "r1",
        "resource_id": None,
        "before_value": None,
        "after_value": None,
    }
    h1 = _compute_hash(GENESIS_HASH, p1, now)

    p2 = {**p1, "action": "a2"}
    h2 = _compute_hash(h1, p2, now)

    p3 = {**p1, "action": "a3"}
    h3 = _compute_hash(h2, p3, now)

    log1 = MagicMock()
    log1.id = uuid4()
    log1.tenant_id = tenant_id
    log1.user_id = None
    log1.action = "a1"
    log1.resource_type = "r1"
    log1.resource_id = None
    log1.before_value = None
    log1.after_value = None
    log1.prev_hash = GENESIS_HASH
    log1.current_hash = h1
    log1.created_at = now

    log2 = MagicMock()
    log2.id = uuid4()
    log2.tenant_id = tenant_id
    log2.user_id = None
    log2.action = "a2"
    log2.resource_type = "r1"
    log2.resource_id = None
    log2.before_value = None
    log2.after_value = None
    log2.prev_hash = h1
    log2.current_hash = h2
    log2.created_at = now

    log3 = MagicMock()
    log3.id = uuid4()
    log3.tenant_id = tenant_id
    log3.user_id = None
    log3.action = "a3"
    log3.resource_type = "r1"
    log3.resource_id = None
    log3.before_value = None
    log3.after_value = None
    log3.prev_hash = h2
    log3.current_hash = h3
    log3.created_at = now

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [log1, log2, log3]
    db.execute = AsyncMock(return_value=result_mock)

    ok = await verify_hash_chain(db, tenant_id)
    assert ok is True


@pytest.mark.asyncio
async def test_verify_hash_chain_tampered_returns_false():
    """篡改后的哈希链应返回 False."""
    db = AsyncMock()

    tenant_id = uuid4()
    now = datetime.now(UTC)

    # 构造 1 条日志，但 current_hash 被篡改
    log1 = MagicMock()
    log1.id = uuid4()
    log1.tenant_id = tenant_id
    log1.user_id = None
    log1.action = "a1"
    log1.resource_type = "r1"
    log1.resource_id = None
    log1.before_value = None
    log1.after_value = None
    log1.prev_hash = GENESIS_HASH
    log1.current_hash = "tampered_hash_value_not_matching_computed" + "0" * 32
    log1.created_at = now

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [log1]
    db.execute = AsyncMock(return_value=result_mock)

    ok = await verify_hash_chain(db, tenant_id)
    assert ok is False


@pytest.mark.asyncio
async def test_verify_hash_chain_broken_link_returns_false():
    """prev_hash 链断裂应返回 False."""
    db = AsyncMock()

    tenant_id = uuid4()
    now = datetime.now(UTC)

    log1 = MagicMock()
    log1.id = uuid4()
    log1.tenant_id = tenant_id
    log1.user_id = None
    log1.action = "a1"
    log1.resource_type = "r1"
    log1.resource_id = None
    log1.before_value = None
    log1.after_value = None
    log1.prev_hash = GENESIS_HASH
    log1.current_hash = "b" * 64
    log1.created_at = now

    # 第二条 prev_hash 与第一条 current_hash 不匹配
    log2 = MagicMock()
    log2.id = uuid4()
    log2.tenant_id = tenant_id
    log2.user_id = None
    log2.action = "a2"
    log2.resource_type = "r1"
    log2.resource_id = None
    log2.before_value = None
    log2.after_value = None
    log2.prev_hash = "c" * 64  # 与 log1.current_hash 不匹配
    log2.current_hash = "d" * 64
    log2.created_at = now

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [log1, log2]
    db.execute = AsyncMock(return_value=result_mock)

    ok = await verify_hash_chain(db, tenant_id)
    assert ok is False


@pytest.mark.asyncio
async def test_verify_hash_chain_empty_returns_true():
    """空哈希链（无记录）应返回 True."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)

    ok = await verify_hash_chain(db, uuid4())
    assert ok is True


# ============================================================
# 2. llm_service 测试
# ============================================================


def test_sanitize_pii_removes_sensitive_fields():
    """sanitize_pii 应移除 id_card/phone/salary/ethnicity/disability."""
    emp = {
        "name": "张三",
        "id_card": "110101199001011234",
        "phone": "13800138000",
        "salary": 25000,
        "salary_encrypted": "xxx",
        "id_card_encrypted": "yyy",
        "phone_encrypted": "zzz",
        "ethnicity": 1,
        "disability": 0,
        "ethnicity_encrypted": "aaa",
        "disability_encrypted": "bbb",
        "department_name": "Sales",
        "position": "Engineer",
    }
    sanitized = sanitize_pii(emp)

    # 姓名脱敏
    assert sanitized["name"] == "员工A"
    # 敏感字段被移除
    for key in [
        "id_card", "phone", "salary", "salary_encrypted",
        "id_card_encrypted", "phone_encrypted",
        "ethnicity", "disability",
        "ethnicity_encrypted", "disability_encrypted",
    ]:
        assert key not in sanitized, f"sanitize_pii 未移除敏感字段：{key}"

    # 部门/岗位保留
    assert sanitized["department_name"] == "Sales"
    assert sanitized["position"] == "Engineer"


def test_sanitize_pii_does_not_mutate_input():
    """sanitize_pii 不应修改原始输入 dict."""
    emp = {"name": "张三", "id_card": "110101199001011234", "phone": "13800138000"}
    _ = sanitize_pii(emp)
    # 原 dict 应保留原值
    assert emp["name"] == "张三"
    assert emp["id_card"] == "110101199001011234"
    assert emp["phone"] == "13800138000"


def test_sanitize_pii_handles_missing_fields():
    """sanitize_pii 应处理缺失字段（不抛 KeyError）."""
    emp = {"name": "李四"}
    sanitized = sanitize_pii(emp)
    assert sanitized["name"] == "员工A"


def test_build_prompt_contains_risk_score_and_top3_factors():
    """build_prompt 应含风险分与 Top3 因子."""
    emp = {"name": "员工A", "department_name": "Sales", "position": "Engineer"}
    factors = [
        {"feature": "salary_percentile", "display_name": "薪资分位", "value": 0.3},
        {"feature": "promotion_gap_months", "display_name": "晋升间隔", "value": 36},
        {"feature": "YearsSinceLastPromotion", "display_name": "上次晋升年限", "value": 5},
    ]
    prompt = build_prompt(emp, factors, risk_score=75)

    # 含风险分
    assert "75" in prompt
    # 含员工姓名
    assert "员工A" in prompt
    # 含部门/岗位
    assert "Sales" in prompt
    assert "Engineer" in prompt
    # 含 Top3 因子
    assert "薪资分位" in prompt
    assert "晋升间隔" in prompt
    # 含调薪/转岗/培训/辅导关键字
    assert "调薪" in prompt or "转岗" in prompt or "培训" in prompt or "辅导" in prompt


def test_build_prompt_uses_feature_name_when_no_display_name():
    """display_name 缺失时应用 feature 字段名."""
    emp = {"name": "员工A", "department_name": "Sales", "position": "Engineer"}
    factors = [{"feature": "Age", "value": 25}]  # 无 display_name
    prompt = build_prompt(emp, factors, risk_score=50)
    assert "Age" in prompt


def test_fallback_template_contains_three_parts():
    """_fallback_template 应含调薪/晋升/辅导三部分."""
    emp = {"name": "员工A", "department_name": "Sales", "position": "Engineer"}
    factors = [
        {"feature": "salary_percentile", "contribution": -0.5},
        {"feature": "promotion_gap_months", "contribution": 0.3},
    ]
    text = _fallback_template(emp, factors, risk_score=80)

    # 含风险分
    assert "80" in text
    # 含调薪建议
    assert "调薪建议" in text
    # 含晋升/培训
    assert "晋升" in text or "培训" in text
    # 含辅导支持
    assert "辅导支持" in text
    # 编号 1/2/3 三部分
    assert "1." in text
    assert "2." in text
    assert "3." in text


def test_fallback_template_low_salary_branch():
    """salary_percentile 贡献为负时应触发调薪 15%-20% 建议."""
    emp = {"name": "员工A"}
    factors = [{"feature": "salary_percentile", "contribution": -0.4}]
    text = _fallback_template(emp, factors, risk_score=70)
    assert "15%-20%" in text


def test_fallback_template_normal_salary_branch():
    """salary_percentile 不在 factors 中时应触发小幅调薪 5%-10%."""
    emp = {"name": "员工A"}
    factors = [{"feature": "Age", "contribution": 0.3}]
    text = _fallback_template(emp, factors, risk_score=60)
    assert "5%-10%" in text


def test_fallback_template_promotion_gap_branch():
    """promotion_gap_months 在 factors 中时应触发晋升评估."""
    emp = {"name": "员工A"}
    factors = [
        {"feature": "salary_percentile", "contribution": -0.4},
        {"feature": "promotion_gap_months", "contribution": 0.3},
    ]
    text = _fallback_template(emp, factors, risk_score=70)
    assert "晋升评估" in text or "晋升/转岗" in text


@pytest.mark.asyncio
async def test_stream_advice_falls_back_to_template_when_no_api_key(monkeypatch):
    """无 DASHSCOPE_API_KEY 时 stream_advice 应走降级模板路径."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")

    emp = {"name": "员工A", "department_name": "Sales", "position": "Engineer"}
    factors = [{"feature": "salary_percentile", "contribution": -0.4}]

    chunks = []
    async for chunk in LLMService.stream_advice(emp, factors, risk_score=75):
        chunks.append(chunk)

    # 应有 chunk + metadata + done
    chunk_types = [next(iter(c)) for c in chunks]
    assert "chunk" in chunk_types
    assert "metadata" in chunk_types
    assert chunks[-1] == {"done": True}

    # metadata 应标注 rule-template
    metadata_chunk = next(c for c in chunks if "metadata" in c)
    assert metadata_chunk["metadata"]["model"] == "rule-template"

    # chunk 文本应包含调薪/晋升/辅导
    text = "".join(c.get("chunk", "") for c in chunks)
    assert "调薪" in text
    assert "辅导" in text


@pytest.mark.asyncio
async def test_stream_advice_fallback_yields_multiple_chunks(monkeypatch):
    """降级路径应 yield 多个 chunk（按段落切分）."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")

    emp = {"name": "员工A"}
    factors = []

    chunks = []
    async for chunk in LLMService.stream_advice(emp, factors, risk_score=50):
        chunks.append(chunk)

    chunk_only = [c for c in chunks if "chunk" in c]
    # 至少有 1 个 chunk
    assert len(chunk_only) >= 1


@pytest.mark.asyncio
async def test_call_dashscope_sse_raises_without_api_key(monkeypatch):
    """_call_dashscope_sse 在 DASHSCOPE_API_KEY 缺失时应抛 RuntimeError."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")

    chunks = []
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        async for chunk in LLMService._call_dashscope_sse("test prompt", "qwen-max"):
            chunks.append(chunk)


# ============================================================
# 3. risk_service 测试（补充 test_risk_service.py 未覆盖部分）
# ============================================================


@pytest.mark.asyncio
async def test_risk_service_predict_with_mocked_db_returns_valid_result(monkeypatch):
    """RiskService.predict 完整流程：mock db + Employee + FusionEngine.

    使用 mock 替换 FusionEngine 与 ShapExplainer 单例，避免依赖真实模型加载。
    """
    from app.services import risk_service

    # 重置单例
    risk_service._reset_singletons()

    # Mock FusionEngine
    fake_engine = MagicMock()
    fake_engine.predict = MagicMock(return_value={
        "risk_score": 72,
        "risk_level": "medium_high",
        "modality_scores": {"structured": 0.8, "behavior": 0.6},
    })
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: fake_engine)

    # Mock ShapExplainer
    fake_explainer = MagicMock()
    fake_explainer.explain = MagicMock(return_value=[
        {"feature": "salary_percentile", "contribution": -0.5, "direction": "negative"},
        {"feature": "promotion_gap_months", "contribution": 0.3, "direction": "positive"},
        {"feature": "YearsSinceLastPromotion", "contribution": 0.2, "direction": "positive"},
    ])
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: fake_explainer)

    # Mock Redis: 返回 None（无缓存）
    monkeypatch.setattr(risk_service, "get_redis", lambda: None)

    # Mock broadcast_risk_update（避免依赖 WebSocket 连接池）
    async def _fake_broadcast(**kwargs):
        pass

    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    # Mock db 与 Employee
    emp_id = uuid4()
    tenant_id = uuid4()

    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"
    fake_employee.level = "P5"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    # patch RiskPrediction 构造（避免 ORM 元数据校验）
    fake_record = MagicMock()
    fake_record.id = uuid4()
    with patch("app.services.risk_service.RiskPrediction") as RiskPredictionMock:
        RiskPredictionMock.return_value = fake_record
        result = await RiskService.predict(emp_id, tenant_id, db=db)

    # 验证返回 dict 含正确字段
    assert result["employee_id"] == str(emp_id)
    assert result["risk_score"] == 72
    assert result["risk_level"] == "medium_high"
    assert result["model_version"] == "fusion-engine-v1"
    assert result["cached"] is False
    assert len(result["shap_factors"]) == 3
    assert "predicted_at" in result
    assert "prediction_id" in result
    assert "modality_scores" in result


@pytest.mark.asyncio
async def test_risk_service_predict_employee_not_found_raises(monkeypatch):
    """Employee 不存在时应抛 ValueError."""
    from app.services import risk_service

    # Mock Redis: None
    monkeypatch.setattr(risk_service, "get_redis", lambda: None)

    # Mock db：返回 None
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    emp_id = uuid4()
    tenant_id = uuid4()
    with pytest.raises(ValueError, match="员工不存在"):
        await RiskService.predict(emp_id, tenant_id, db=db)


@pytest.mark.asyncio
async def test_risk_service_predict_redis_cache_hit_returns_cached_true(monkeypatch):
    """Redis 缓存命中时应返回 cached=True."""
    from app.services import risk_service

    # 重置单例
    risk_service._reset_singletons()

    emp_id = uuid4()
    tenant_id = uuid4()
    cached_payload = {
        "prediction_id": "cached-id",
        "employee_id": str(emp_id),
        "risk_score": 65,
        "risk_level": "medium_high",
        "modality_scores": {"structured": 0.7, "behavior": 0.5},
        "model_version": "fusion-engine-v1",
        "predicted_at": "2026-01-01T00:00:00+00:00",
        "cached": False,
        "shap_factors": [],
    }

    # Mock Redis：返回 cached payload
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=json.dumps(cached_payload))
    monkeypatch.setattr(risk_service, "get_redis", lambda: fake_redis)

    # db 不应被调用（缓存命中后直接返回）
    db = AsyncMock()

    result = await RiskService.predict(emp_id, tenant_id, db=db, force_refresh=False)

    assert result["cached"] is True
    assert result["risk_score"] == 65
    assert result["employee_id"] == str(emp_id)
    # db.execute 不应被调用
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_risk_service_predict_force_refresh_skips_cache(monkeypatch):
    """force_refresh=True 时应跳过缓存查询."""
    from app.services import risk_service

    risk_service._reset_singletons()

    # Mock FusionEngine 返回占位（engine=None 路径）
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: None)
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: None)

    # Mock Redis
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock()
    monkeypatch.setattr(risk_service, "get_redis", lambda: fake_redis)

    # Mock broadcast_risk_update
    async def _fake_broadcast(**kwargs):
        pass
    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    emp_id = uuid4()
    tenant_id = uuid4()
    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"
    fake_employee.level = "P5"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.services.risk_service.RiskPrediction") as RiskPredictionMock:
        RiskPredictionMock.return_value = MagicMock(id=uuid4())
        result = await RiskService.predict(emp_id, tenant_id, db=db, force_refresh=True)

    # force_refresh=True 时 Redis get 不应被调用
    fake_redis.get.assert_not_awaited()
    # 返回占位风险分 50（engine=None 路径）
    assert result["risk_score"] == 50
    assert result["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_risk_service_predict_redis_get_failure_skips_cache(monkeypatch):
    """Redis get 异常时应跳过缓存，继续走正常流程."""
    from app.services import risk_service

    risk_service._reset_singletons()

    # Mock FusionEngine 返回 None（占位路径）
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: None)
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: None)

    # Mock Redis：get 抛异常
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
    fake_redis.set = AsyncMock()
    monkeypatch.setattr(risk_service, "get_redis", lambda: fake_redis)

    async def _fake_broadcast(**kwargs):
        pass
    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    emp_id = uuid4()
    tenant_id = uuid4()
    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"
    fake_employee.level = "P5"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.services.risk_service.RiskPrediction") as RiskPredictionMock:
        RiskPredictionMock.return_value = MagicMock(id=uuid4())
        result = await RiskService.predict(emp_id, tenant_id, db=db)

    # Redis 异常不阻塞主流程
    assert result["risk_score"] == 50
    assert result["cached"] is False


@pytest.mark.asyncio
async def test_risk_service_predict_redis_set_failure_does_not_block(monkeypatch):
    """Redis set 异常不应阻塞主流程（仅 log warning）."""
    from app.services import risk_service

    risk_service._reset_singletons()
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: None)
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: None)

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock(side_effect=RuntimeError("redis write fail"))
    monkeypatch.setattr(risk_service, "get_redis", lambda: fake_redis)

    async def _fake_broadcast(**kwargs):
        pass
    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    emp_id = uuid4()
    tenant_id = uuid4()
    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"
    fake_employee.level = "P5"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.services.risk_service.RiskPrediction") as RiskPredictionMock:
        RiskPredictionMock.return_value = MagicMock(id=uuid4())
        result = await RiskService.predict(emp_id, tenant_id, db=db)

    # Redis 写失败不阻塞
    assert result["risk_score"] == 50


@pytest.mark.asyncio
async def test_risk_service_predict_db_flush_failure_raises(monkeypatch):
    """db.flush 异常时应向上抛出（预测落库是核心功能，不再静默吞掉）."""
    from app.services import risk_service

    risk_service._reset_singletons()
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: None)
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: None)

    monkeypatch.setattr(risk_service, "get_redis", lambda: None)

    async def _fake_broadcast(**kwargs):
        pass
    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    emp_id = uuid4()
    tenant_id = uuid4()
    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"
    fake_employee.level = "P5"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock(side_effect=RuntimeError("flush failed"))

    with pytest.raises(RuntimeError, match="flush failed"):
        await RiskService.predict(emp_id, tenant_id, db=db)


@pytest.mark.asyncio
async def test_risk_service_predict_fusion_engine_exception_falls_back(monkeypatch):
    """FusionEngine.predict 异常时应降级为占位风险分 50."""
    from app.services import risk_service

    risk_service._reset_singletons()

    # Mock FusionEngine.predict 抛异常
    fake_engine = MagicMock()
    fake_engine.predict = MagicMock(side_effect=RuntimeError("model inference fail"))
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: fake_engine)
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: None)
    monkeypatch.setattr(risk_service, "get_redis", lambda: None)

    async def _fake_broadcast(**kwargs):
        pass
    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    emp_id = uuid4()
    tenant_id = uuid4()
    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"
    fake_employee.level = "P5"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.services.risk_service.RiskPrediction") as RiskPredictionMock:
        RiskPredictionMock.return_value = MagicMock(id=uuid4())
        result = await RiskService.predict(emp_id, tenant_id, db=db)

    # 降级为占位 50
    assert result["risk_score"] == 50
    assert result["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_risk_service_predict_shap_exception_returns_empty_factors(monkeypatch):
    """ShapExplainer.explain 异常时 shap_factors 应为空列表."""
    from app.services import risk_service

    risk_service._reset_singletons()

    fake_engine = MagicMock()
    fake_engine.predict = MagicMock(return_value={
        "risk_score": 72,
        "risk_level": "medium_high",
        "modality_scores": {"structured": 0.8, "behavior": 0.6},
    })
    monkeypatch.setattr(risk_service, "_get_fusion_engine", lambda: fake_engine)

    fake_explainer = MagicMock()
    fake_explainer.explain = MagicMock(side_effect=RuntimeError("shap fail"))
    monkeypatch.setattr(risk_service, "_get_shap_explainer", lambda: fake_explainer)

    monkeypatch.setattr(risk_service, "get_redis", lambda: None)

    async def _fake_broadcast(**kwargs):
        pass
    monkeypatch.setattr("app.api.v1.ws.broadcast_risk_update", _fake_broadcast)

    emp_id = uuid4()
    tenant_id = uuid4()
    fake_employee = MagicMock()
    fake_employee.id = emp_id
    fake_employee.tenant_id = tenant_id
    fake_employee.birth_date = None
    fake_employee.hire_date = None
    fake_employee.salary_percentile = None
    fake_employee.position = "Sales"
    fake_employee.level = "P5"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_employee
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.services.risk_service.RiskPrediction") as RiskPredictionMock:
        RiskPredictionMock.return_value = MagicMock(id=uuid4())
        result = await RiskService.predict(emp_id, tenant_id, db=db)

    assert result["shap_factors"] == []
    # 风险分仍正常
    assert result["risk_score"] == 72


@pytest.mark.asyncio
async def test_risk_service_predict_kill_switch_active_returns_degraded(monkeypatch):
    """Kill Switch 激活时 RiskService.predict 应返回降级结果."""
    from app.core import kill_switch

    async def _mock_active():
        return True
    monkeypatch.setattr(kill_switch, "is_active_async", _mock_active)

    emp_id = uuid4()
    tenant_id = uuid4()
    result = await RiskService.predict(emp_id, tenant_id, db=None)

    assert result["kill_switch"] is True
    assert result["risk_score"] == 50
    assert result["risk_level"] == "medium"
    assert result["model_version"] == "kill-switch-active"
    assert result["cached"] is False
    assert result["shap_factors"] == []
    assert result["employee_id"] == str(emp_id)
    assert result["prediction_id"] is None


@pytest.mark.asyncio
async def test_risk_service_predict_kill_switch_check_failure_fail_open(monkeypatch):
    """Kill Switch 检查异常时应 fail-open（继续正常流程，无 db 抛 ValueError）."""
    from app.core import kill_switch

    async def _mock_raises():
        raise RuntimeError("redis down")
    monkeypatch.setattr(kill_switch, "is_active_async", _mock_raises)

    emp_id = uuid4()
    tenant_id = uuid4()
    # 无 db → ValueError
    with pytest.raises(ValueError, match="db"):
        await RiskService.predict(emp_id, tenant_id, db=None)


@pytest.mark.asyncio
async def test_risk_service_global_explanation_no_db_returns_default():
    """global_explanation 无 db 时应返回默认占位."""
    result = await RiskService.global_explanation(uuid4(), window_days=30, db=None)

    assert result["model_version"] == "fusion-engine-v1"
    assert result["window_days"] == 30
    assert len(result["top_features"]) > 0
    assert "computed_at" in result
    # 默认 Top3
    assert len(result["top_features"]) == 3
    for f in result["top_features"]:
        assert "feature" in f
        assert "contribution" in f
        assert "direction" in f


@pytest.mark.asyncio
async def test_risk_service_global_explanation_no_records_returns_default(monkeypatch):
    """DB 中无预测记录时应返回默认占位."""
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    result = await RiskService.global_explanation(uuid4(), window_days=30, db=db)

    assert result["model_version"] == "fusion-engine-v1"
    assert len(result["top_features"]) == 3


@pytest.mark.asyncio
async def test_risk_service_global_explanation_with_records_returns_top_features(monkeypatch):
    """DB 有记录时应返回聚合 Top 特征."""
    # 构造 5 条预测记录，含特征值
    records = []
    for i in range(5):
        r = MagicMock()
        r.feature_values = {
            "salary_percentile": 0.3 + i * 0.1,
            "promotion_gap_months": float(i * 12),
            "YearsSinceLastPromotion": float(i * 3),
        }
        records.append(r)

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = records
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    result = await RiskService.global_explanation(uuid4(), window_days=30, db=db)

    assert result["model_version"] == "fusion-engine-v1"
    assert "top_features" in result
    # 应有聚合特征（不空）
    assert len(result["top_features"]) > 0
    # 每个特征含必要字段
    for f in result["top_features"]:
        assert "feature" in f
        assert "contribution" in f
        assert "direction" in f
        assert "display_name" in f


@pytest.mark.asyncio
async def test_risk_service_global_explanation_db_exception_returns_default(monkeypatch):
    """DB 异常时应返回默认占位（不抛异常）."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))

    result = await RiskService.global_explanation(uuid4(), window_days=30, db=db)

    assert result["model_version"] == "fusion-engine-v1"
    assert len(result["top_features"]) == 3


def test_score_to_level_negative_score_returns_low():
    """负分数应返回 low（边界保护）."""
    assert RiskService.score_to_level(-5) == "low"
    assert RiskService.score_to_level(0) == "low"


def test_score_to_level_above_max_returns_high():
    """超过 100 的分数应返回 high."""
    assert RiskService.score_to_level(101) == "high"
    assert RiskService.score_to_level(150) == "high"


def test_score_to_level_mid_thresholds():
    """中段阈值覆盖."""
    assert RiskService.score_to_level(25) == "medium_low"
    assert RiskService.score_to_level(50) == "medium"
    assert RiskService.score_to_level(70) == "medium_high"


def test_get_feature_display_name_returns_chinese():
    """get_feature_display_name 应返回中文显示名."""
    assert get_feature_display_name("Age") == "年龄"
    assert get_feature_display_name("salary_percentile") == "薪资分位"
    # 未知特征应原样返回
    assert get_feature_display_name("unknown_feature") == "unknown_feature"


def test_get_feature_display_name_covers_all_structured_features():
    """所有 STRUCTURED_FEATURE_COLUMNS 应有中文显示名映射."""
    from app.ml.feature_engineering import STRUCTURED_FEATURE_COLUMNS
    from app.services.risk_service import _FEATURE_DISPLAY_NAMES

    for col in STRUCTURED_FEATURE_COLUMNS:
        assert col in _FEATURE_DISPLAY_NAMES, f"特征 {col} 缺少中文显示名"
