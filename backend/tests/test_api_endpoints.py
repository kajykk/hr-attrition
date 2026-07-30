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
    """GET /health 应返回 200 与 status=healthy."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
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


def test_refresh_with_missing_field_returns_422(client):
    """refresh 端点缺少 refresh_token 字段应返回 422."""
    resp = client.post(
        "/api/v1/auth/refresh",
        json={},
    )
    assert resp.status_code == 422
