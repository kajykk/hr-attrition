"""API 端点测试 - 健康检查 / 根路径 / 认证守卫 / Kill Switch / 预警 / 404 / CORS.

使用 TestClient（已在 conftest.py 配置）。覆盖：
  - GET /health 与 GET / 的公开端点
  - 需认证端点（无 token → 401/403）
  - GET /api/v1/admin/kill-switch 状态查询
  - GET /api/v1/warnings 需认证
  - 无效路由 → 404
  - CORS 头
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.core.security import create_access_token


# ===== 共享辅助：覆盖 get_db 依赖，避免触发真实 PostgreSQL 连接 =====


async def _fake_async_gen_yield_none():
    """模拟 get_db 依赖：yield None（不连接真实 DB）."""
    yield None


# ============================================================
# 1. 公开端点
# ============================================================


def test_health_returns_200_and_healthy(client):
    """GET /health 应返回 200 与 status=healthy（或依赖缺失时 degraded）."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("healthy", "degraded")
    assert "components" in body
    assert "version" in body


def test_root_returns_app_hra(client):
    """GET / 应返回 app=HRA."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == "HRA"
    assert "version" in body
    assert "docs" in body


def test_openapi_spec_available(client):
    """/openapi.json 应可访问."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"].startswith("HRA")


def test_docs_page_available(client):
    """/docs Swagger UI 应可访问."""
    resp = client.get("/docs")
    # /docs 返回 HTML 页面
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_redoc_page_available(client):
    """/redoc ReDoc 应可访问."""
    resp = client.get("/redoc")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


# ============================================================
# 2. 认证守卫测试
# ============================================================


def test_global_explanation_requires_auth(client):
    """GET /api/v1/risk/global-explanation 无 token 应返回 401."""
    resp = client.get("/api/v1/risk/global-explanation")
    assert resp.status_code == 401


def test_warnings_list_requires_auth(client):
    """GET /api/v1/warnings 无 token 应返回 401."""
    resp = client.get("/api/v1/warnings")
    assert resp.status_code == 401


def test_warnings_detail_requires_auth(client):
    """GET /api/v1/warnings/{id} 无 token 应返回 401."""
    resp = client.get(f"/api/v1/warnings/{uuid4()}")
    assert resp.status_code == 401


def test_risk_predict_requires_auth(client):
    """POST /api/v1/risk/predict 无 token 应返回 401."""
    resp = client.post(
        "/api/v1/risk/predict",
        json={"employee_id": str(uuid4())},
    )
    assert resp.status_code == 401


def test_employees_list_requires_auth(client):
    """GET /api/v1/employees 无 token 应返回 401."""
    resp = client.get("/api/v1/employees")
    assert resp.status_code == 401


def test_admin_kill_switch_requires_auth(client):
    """GET /api/v1/admin/kill-switch 无 token 应返回 401."""
    resp = client.get("/api/v1/admin/kill-switch")
    assert resp.status_code == 401


def test_admin_kill_switch_activate_requires_auth(client):
    """POST /api/v1/admin/kill-switch/activate 无 token 应返回 401."""
    resp = client.post(
        "/api/v1/admin/kill-switch/activate",
        json={"reason": "test"},
    )
    assert resp.status_code == 401


def test_invalid_bearer_token_returns_401(client):
    """无效 Bearer token 应返回 401."""
    resp = client.get(
        "/api/v1/risk/global-explanation",
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert resp.status_code == 401


def test_malformed_authorization_header_returns_401(client):
    """格式错误的 Authorization 头应返回 401."""
    resp = client.get(
        "/api/v1/risk/global-explanation",
        headers={"Authorization": "NotBearer abc"},
    )
    assert resp.status_code == 401


# ============================================================
# 3. Kill Switch 状态端点（带 token）
# ============================================================


def test_admin_kill_switch_status_with_token(client, monkeypatch):
    """GET /api/v1/admin/kill-switch 带 token 应返回状态（mock 用户与 kill_switch）."""
    # mock get_current_user 依赖：直接 patch deps 模块
    from app.api import deps
    from app.db.session import get_db
    from app.models.user import User

    fake_user = MagicMock(spec=User)
    fake_user.id = uuid4()
    fake_user.tenant_id = uuid4()
    fake_user.role = "admin"
    fake_user.status = "active"

    async def _fake_get_current_user():
        return fake_user

    # mock kill_switch.get_status_async
    from app.core import kill_switch

    async def _fake_status():
        return {"active": False, "reason": "", "activated_at": "", "activated_by": ""}

    monkeypatch.setattr(kill_switch, "get_status_async", _fake_status)

    # 用 dependency_overrides 覆盖 get_current_user 与 get_db（避免触发真实 DB 连接）
    from app.main import app

    app.dependency_overrides[deps.get_current_user] = _fake_get_current_user
    # get_db 在 get_current_user 内部被 Depends 引用，覆盖后不会执行真实连接
    app.dependency_overrides[get_db] = _fake_async_gen_yield_none

    token = create_access_token(str(fake_user.id), str(fake_user.tenant_id), "admin")
    resp = client.get(
        "/api/v1/admin/kill-switch",
        headers={"Authorization": f"Bearer {token}"},
    )

    # 清理 dependency_overrides
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is False


# ============================================================
# 3.5 治理端点：漂移检测 / 公平性监测（D03 4.5 + D10 7.3）
# ============================================================


def test_admin_drift_requires_auth(client):
    """GET /api/v1/admin/drift 无 token 应返回 401."""
    resp = client.get("/api/v1/admin/drift")
    assert resp.status_code == 401


def test_admin_fairness_requires_auth(client):
    """GET /api/v1/admin/fairness 无 token 应返回 401."""
    resp = client.get("/api/v1/admin/fairness")
    assert resp.status_code == 401


def _login_admin_override(app):
    """复用 kill-switch 测试的 admin 身份覆盖（get_current_user + get_db）. """
    from app.api import deps
    from app.db.session import get_db
    from app.models.user import User

    fake_user = MagicMock(spec=User)
    fake_user.id = uuid4()
    fake_user.tenant_id = uuid4()
    fake_user.role = "admin"
    fake_user.status = "active"

    async def _fake_get_current_user():
        return fake_user

    app.dependency_overrides[deps.get_current_user] = _fake_get_current_user
    app.dependency_overrides[get_db] = _fake_async_gen_yield_none


def test_admin_drift_with_token(client, monkeypatch):
    """GET /api/v1/admin/drift 带 token 应返回契约字段（mock detect_drift）."""
    from app.main import app

    _login_admin_override(app)
    try:
        monkeypatch.setattr(
            "app.api.v1.admin.detect_drift",
            lambda: {
                "status": "ok",
                "max_psi": 0.14,
                "critical_features": ["overtime_hours_30d"],
                "features": [
                    {"feature": "overtime_hours_30d", "psi": 0.14},
                    {"feature": "days_since_last_login", "psi": 0.09},
                ],
                "checked_at": "2026-08-01T02:00:00+00:00",
            },
        )
        resp = client.get(
            "/api/v1/admin/drift",
            headers={"Authorization": f"Bearer {create_access_token(str(uuid4()), str(uuid4()), 'admin')}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["max_psi"] == 0.14
        assert body["critical_features"] == ["overtime_hours_30d"]
        assert body["features"][0]["feature"] == "overtime_hours_30d"
        assert "computed_at" in body
    finally:
        app.dependency_overrides.clear()


def test_admin_drift_skipped_returns_502(client, monkeypatch):
    """漂移检测数据不可用（skipped）时应返回 502 与原因."""
    from app.main import app

    _login_admin_override(app)
    try:
        monkeypatch.setattr(
            "app.api.v1.admin.detect_drift",
            lambda: {"status": "skipped", "reason": "基线数据不可用", "checked_at": "2026-08-01T02:00:00+00:00"},
        )
        resp = client.get(
            "/api/v1/admin/drift",
            headers={"Authorization": f"Bearer {create_access_token(str(uuid4()), str(uuid4()), 'admin')}"},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "基线数据不可用"
    finally:
        app.dependency_overrides.clear()


def test_admin_fairness_with_token(client, monkeypatch):
    """GET /api/v1/admin/fairness 应返回 4 维度偏差（百分比换算）."""
    from app.main import app

    _login_admin_override(app)
    try:
        monkeypatch.setattr(
            "app.api.v1.admin.fairness_daily_report",
            lambda: {
                "status": "ok",
                "dimensions": {
                    "gender": {"parity_difference": 0.032, "passed": True, "label": "性别 (M/F)"},
                    "age": {"parity_difference": 0.048, "passed": True, "label": "年龄 (<35 / >=35)"},
                    "ethnicity": {"parity_difference": 0.021, "passed": True, "label": "民族 (汉族/少数民族)"},
                    "disability": {"parity_difference": 0.015, "passed": True, "label": "残障 (0/1)"},
                },
                "checked_at": "2026-08-01T03:00:00+00:00",
            },
        )
        resp = client.get(
            "/api/v1/admin/fairness",
            headers={"Authorization": f"Bearer {create_access_token(str(uuid4()), str(uuid4()), 'admin')}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["dimensions"]) == 4
        by_name = {d["name"]: d for d in body["dimensions"]}
        assert by_name["gender"]["disparity"] == 3.2  # 0.032 * 100 四舍五入
        assert by_name["age"]["disparity"] == 4.8
        assert by_name["disability"]["label"] == "残障 (0/1)"
    finally:
        app.dependency_overrides.clear()


def test_admin_fairness_skipped_returns_502(client, monkeypatch):
    """公平性数据不可用（skipped）时应返回 502."""
    from app.main import app

    _login_admin_override(app)
    try:
        monkeypatch.setattr(
            "app.api.v1.admin.fairness_daily_report",
            lambda: {"status": "skipped", "reason": "公平性数据文件不存在", "checked_at": "2026-08-01T03:00:00+00:00"},
        )
        resp = client.get(
            "/api/v1/admin/fairness",
            headers={"Authorization": f"Bearer {create_access_token(str(uuid4()), str(uuid4()), 'admin')}"},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "公平性数据文件不存在"
    finally:
        app.dependency_overrides.clear()


# ============================================================
# 4. 404 与无效路由
# ============================================================


def test_nonexistent_route_returns_404(client):
    """不存在的路由应返回 404."""
    resp = client.get("/api/v1/nonexistent-endpoint")
    assert resp.status_code == 404


def test_nonexistent_root_path_returns_404(client):
    """根路径下不存在的路由应返回 404."""
    resp = client.get("/nonexistent-root-path")
    assert resp.status_code == 404


def test_method_not_allowed(client):
    """不支持的方法应返回 405（POST /health 不允许）."""
    resp = client.post("/health")
    assert resp.status_code == 405


# ============================================================
# 5. CORS 头
# ============================================================


def test_cors_headers_on_preflight(client):
    """OPTIONS 预检请求应返回 CORS 头."""
    resp = client.options(
        "/api/v1/risk/global-explanation",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    # 预检请求应成功（CORS 中间件处理）
    assert resp.status_code in (200, 204)
    # 应有 CORS 响应头
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "GET" in resp.headers.get("access-control-allow-methods", "")


def test_cors_allow_origin_on_actual_request(client):
    """实际请求应携带 CORS allow-origin 头."""
    resp = client.get(
        "/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200
    # CORS 中间件应在响应中添加 allow-origin
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


# ============================================================
# 6. Auth 登录端点
# ============================================================


def test_login_with_invalid_email_returns_401(client):
    """不存在的邮箱登录应返回 401（mock get_db 返回空结果集）."""
    from unittest.mock import AsyncMock, MagicMock

    from app.db.session import get_db
    from app.main import app

    # mock db.execute 返回空结果（用户不存在）
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)

    async def _fake_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "wrong"},
        )
        # 用户不存在 → 401
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_login_with_missing_fields_returns_422(client):
    """登录请求缺少字段应返回 422（Pydantic 校验失败）."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com"},  # 缺 password
    )
    assert resp.status_code == 422


def test_refresh_with_invalid_token_returns_401(client):
    """refresh 端点用无效 token 应返回 401."""
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "invalid.token.here"},
    )
    assert resp.status_code == 401


# ============================================================
# 7. 登录限流（P1-6）
# ============================================================


def test_login_rate_limit_returns_429(client):
    """连续超过 RATE_LIMIT_LOGIN 次登录应返回 429（按 IP 计数）. """
    from unittest.mock import AsyncMock, MagicMock

    from app.core.rate_limit import limiter
    from app.db.session import get_db
    from app.main import app

    # 覆盖 get_db：用户不存在（401），避免连真实 PostgreSQL
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)

    async def _fake_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        limiter.reset()
        responses = []
        # 默认 5/minute，打 6 次
        for _ in range(6):
            responses.append(
                client.post(
                    "/api/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "x"},
                )
            )
        assert responses[5].status_code == 429
        assert responses[5].headers.get("retry-after")
    finally:
        limiter.reset()
        app.dependency_overrides.clear()


# ============================================================
# 8. 登录失败审计（P2-10 回归：失败路径必须落库，冒烟发现回滚缺陷）
# ============================================================


def _fake_user(role="admin", totp_secret=None):
    from types import SimpleNamespace
    from uuid import uuid4

    from app.core.security import hash_password

    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        role=role,
        status="active",
        totp_secret=totp_secret,
        password_hash=hash_password("correct-password"),
    )


def test_login_failure_writes_audit_log(client, monkeypatch):
    """错误密码登录必须写 auth.login_failed 审计（commit=True 防回滚丢失）. """
    from unittest.mock import AsyncMock, MagicMock

    from app.db.session import get_db
    from app.main import app

    calls: list[dict] = []

    async def fake_append_audit_log(**kwargs):
        calls.append(kwargs)

    import app.api.v1.auth as auth_mod

    monkeypatch.setattr(auth_mod, "append_audit_log", fake_append_audit_log)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = _fake_user()
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)
    db_mock.commit = AsyncMock()

    async def _fake_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "wrong-password"},
        )
        assert resp.status_code == 401
        assert any(c.get("action") == "auth.login_failed" for c in calls)
        assert any(c.get("after_value") == {"reason": "bad_credentials"} for c in calls)
        # commit=True：显式提交，防止 get_db 在 401 抛异常时 rollback
        assert db_mock.commit.await_count >= 1
    finally:
        app.dependency_overrides.clear()


def test_login_invalid_totp_writes_audit_log(client, monkeypatch):
    """2FA 校验失败必须写 auth.login_failed（reason=invalid_totp）审计. """
    from unittest.mock import AsyncMock, MagicMock

    from app.db.session import get_db
    from app.main import app

    calls: list[dict] = []

    async def fake_append_audit_log(**kwargs):
        calls.append(kwargs)

    import app.api.v1.auth as auth_mod

    monkeypatch.setattr(auth_mod, "append_audit_log", fake_append_audit_log)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = _fake_user(totp_secret="BASE32SECRET1234567890")
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)
    db_mock.commit = AsyncMock()

    async def _fake_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "correct-password", "totp_code": "000000"},
        )
        assert resp.status_code == 401
        assert any(c.get("action") == "auth.login_failed" and
                   c.get("after_value") == {"reason": "invalid_totp"} for c in calls)
    finally:
        app.dependency_overrides.clear()


def test_refresh_with_missing_field_returns_422(client):
    """refresh 端点缺少 refresh_token 字段应返回 422."""
    resp = client.post(
        "/api/v1/auth/refresh",
        json={},
    )
    assert resp.status_code == 422
