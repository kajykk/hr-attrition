"""租户上下文测试 - ContextVar + 中间件 + require_tenant_header 依赖.

覆盖：
  - set_tenant_context / get_tenant_context / clear_tenant_context 基本流程
  - get_current_tenant_id 无上下文时抛 403
  - TenantContext dataclass 字段
  - require_tenant_header 依赖（缺失 X-Tenant-Id → 403）
  - tenant_middleware 中间件（仅 JWT 注入；X-Tenant-Id 不受信任）
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.tenant import (
    TenantContext,
    clear_tenant_context,
    get_current_tenant_id,
    get_tenant_context,
    require_tenant_header,
    set_tenant_context,
)

# ============================================================
# 1. TenantContext dataclass 测试
# ============================================================


def test_tenant_context_dataclass_fields():
    """TenantContext 应含 tenant_id/user_id/role 字段."""
    ctx = TenantContext(tenant_id="t-001", user_id="u-001", role="admin")
    assert ctx.tenant_id == "t-001"
    assert ctx.user_id == "u-001"
    assert ctx.role == "admin"


def test_tenant_context_dataclass_optional_fields():
    """TenantContext 的 user_id/role 应为可选（默认 None）."""
    ctx = TenantContext(tenant_id="t-001")
    assert ctx.tenant_id == "t-001"
    assert ctx.user_id is None
    assert ctx.role is None


def test_tenant_context_dataclass_equality():
    """TenantContext 同字段值应相等."""
    ctx1 = TenantContext(tenant_id="t-001", user_id="u-001")
    ctx2 = TenantContext(tenant_id="t-001", user_id="u-001")
    assert ctx1 == ctx2


# ============================================================
# 2. set/get/clear 上下文测试
# ============================================================


def test_set_and_get_tenant_context():
    """set_tenant_context 后 get_tenant_context 应返回相同上下文."""
    clear_tenant_context()  # 确保起始为空
    ctx = TenantContext(tenant_id="t-001", user_id="u-001", role="hrbp")
    set_tenant_context(ctx)

    got = get_tenant_context()
    assert got is ctx
    assert got.tenant_id == "t-001"
    assert got.user_id == "u-001"
    assert got.role == "hrbp"

    clear_tenant_context()


def test_get_tenant_context_default_none():
    """无上下文时 get_tenant_context 应返回 None."""
    clear_tenant_context()
    assert get_tenant_context() is None


def test_clear_tenant_context_resets_to_none():
    """clear_tenant_context 应将上下文重置为 None."""
    set_tenant_context(TenantContext(tenant_id="t-001"))
    assert get_tenant_context() is not None

    clear_tenant_context()
    assert get_tenant_context() is None


def test_set_tenant_context_overwrites_previous():
    """set_tenant_context 应覆盖之前的上下文."""
    clear_tenant_context()
    ctx1 = TenantContext(tenant_id="t-001", user_id="u-001")
    set_tenant_context(ctx1)

    ctx2 = TenantContext(tenant_id="t-002", user_id="u-002")
    set_tenant_context(ctx2)

    got = get_tenant_context()
    assert got is ctx2
    assert got.tenant_id == "t-002"

    clear_tenant_context()


# ============================================================
# 3. get_current_tenant_id 测试
# ============================================================


def test_get_current_tenant_id_returns_id_when_set():
    """上下文存在时 get_current_tenant_id 应返回 tenant_id."""
    clear_tenant_context()
    set_tenant_context(TenantContext(tenant_id="t-001", user_id="u-001"))
    assert get_current_tenant_id() == "t-001"
    clear_tenant_context()


def test_get_current_tenant_id_raises_403_when_no_context():
    """无上下文时 get_current_tenant_id 应抛 403."""
    clear_tenant_context()
    with pytest.raises(HTTPException) as exc_info:
        get_current_tenant_id()
    assert exc_info.value.status_code == 403
    assert "租户上下文缺失" in exc_info.value.detail
    clear_tenant_context()


def test_get_current_tenant_id_raises_403_when_tenant_id_empty():
    """tenant_id 为空字符串时 get_current_tenant_id 应抛 403."""
    clear_tenant_context()
    set_tenant_context(TenantContext(tenant_id=""))
    with pytest.raises(HTTPException) as exc_info:
        get_current_tenant_id()
    assert exc_info.value.status_code == 403
    clear_tenant_context()


# ============================================================
# 4. require_tenant_header 依赖测试
# ============================================================


def test_require_tenant_header_returns_value_when_present():
    """require_tenant_header 在 X-Tenant-Id 存在时应返回该值."""
    result = require_tenant_header(x_tenant_id="t-001")
    assert result == "t-001"


def test_require_tenant_header_raises_403_when_missing():
    """require_tenant_header 在 X-Tenant-Id 缺失时应抛 403."""
    with pytest.raises(HTTPException) as exc_info:
        require_tenant_header(x_tenant_id=None)
    assert exc_info.value.status_code == 403
    assert "X-Tenant-Id" in exc_info.value.detail


def test_require_tenant_header_raises_403_when_empty():
    """require_tenant_header 在 X-Tenant-Id 为空字符串时应抛 403."""
    with pytest.raises(HTTPException) as exc_info:
        require_tenant_header(x_tenant_id="")
    assert exc_info.value.status_code == 403


# ============================================================
# 5. tenant_middleware 中间件测试
# ============================================================


@pytest.mark.asyncio
async def test_tenant_middleware_x_tenant_id_header_does_not_inject_context():
    """X-Tenant-Id 头不再受信任：仅凭该头不得注入租户上下文（防客户端伪造越权）."""
    from app.core.tenant import tenant_middleware

    # 构造 mock request 与 call_next
    class _FakeRequest:
        def __init__(self, headers_dict):
            self.headers = headers_dict

    class _FakeResponse:
        status_code = 200

    async def _fake_call_next(request):
        # 在 call_next 内验证上下文未被注入
        ctx = get_tenant_context()
        assert ctx is None
        return _FakeResponse()

    request = _FakeRequest({"X-Tenant-Id": "t-from-header"})
    # 调用中间件
    response = await tenant_middleware(request, _fake_call_next)
    assert response.status_code == 200

    # 中间件 finally 块应清除上下文
    assert get_tenant_context() is None


@pytest.mark.asyncio
async def test_tenant_middleware_no_headers_does_not_inject_context():
    """无 Authorization 头时中间件不注入上下文（X-Tenant-Id 不再受信任）."""
    from app.core.tenant import tenant_middleware

    class _FakeRequest:
        def __init__(self, headers_dict):
            self.headers = headers_dict

    class _FakeResponse:
        status_code = 200

    async def _fake_call_next(request):
        # 无头时上下文应为 None
        assert get_tenant_context() is None
        return _FakeResponse()

    request = _FakeRequest({})
    response = await tenant_middleware(request, _fake_call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_tenant_middleware_jwt_bearer_injects_context():
    """中间件应从 Bearer JWT 注入租户上下文."""
    from app.core.security import create_access_token
    from app.core.tenant import tenant_middleware

    class _FakeRequest:
        def __init__(self, headers_dict):
            self.headers = headers_dict

    class _FakeResponse:
        status_code = 200

    user_id = str(uuid4())
    tenant_id = str(uuid4())
    token = create_access_token(user_id, tenant_id, "admin")

    async def _fake_call_next(request):
        ctx = get_tenant_context()
        assert ctx is not None
        assert ctx.tenant_id == tenant_id
        assert ctx.user_id == user_id
        assert ctx.role == "admin"
        return _FakeResponse()

    request = _FakeRequest({"Authorization": f"Bearer {token}"})
    response = await tenant_middleware(request, _fake_call_next)
    assert response.status_code == 200

    # finally 块清除上下文
    assert get_tenant_context() is None


@pytest.mark.asyncio
async def test_tenant_middleware_invalid_jwt_does_not_inject_context():
    """无效 JWT 时中间件不注入上下文（由端点依赖拦截）."""
    from app.core.tenant import tenant_middleware

    class _FakeRequest:
        def __init__(self, headers_dict):
            self.headers = headers_dict

    class _FakeResponse:
        status_code = 200

    async def _fake_call_next(request):
        # 无效 token 时上下文应为 None
        assert get_tenant_context() is None
        return _FakeResponse()

    request = _FakeRequest({"Authorization": "Bearer invalid.token.here"})
    response = await tenant_middleware(request, _fake_call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_tenant_middleware_x_tenant_id_does_not_override_jwt():
    """JWT 已注入上下文时 X-Tenant-Id 头被忽略（不覆盖、不参与注入）."""
    from app.core.security import create_access_token
    from app.core.tenant import tenant_middleware

    class _FakeRequest:
        def __init__(self, headers_dict):
            self.headers = headers_dict

    class _FakeResponse:
        status_code = 200

    user_id = str(uuid4())
    tenant_id = str(uuid4())
    token = create_access_token(user_id, tenant_id, "admin")

    async def _fake_call_next(request):
        ctx = get_tenant_context()
        # JWT 优先，X-Tenant-Id 不覆盖
        assert ctx.tenant_id == tenant_id
        return _FakeResponse()

    request = _FakeRequest({
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": "should-not-override",
    })
    response = await tenant_middleware(request, _fake_call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_tenant_middleware_clears_context_on_exception():
    """call_next 抛异常时中间件 finally 块仍应清除上下文."""
    from app.core.security import create_access_token
    from app.core.tenant import tenant_middleware

    class _FakeRequest:
        def __init__(self, headers_dict):
            self.headers = headers_dict

    class _FakeResponse:
        status_code = 200

    token = create_access_token(str(uuid4()), str(uuid4()), "hrbp")

    async def _fake_call_next(request):
        # 上下文已注入
        assert get_tenant_context() is not None
        raise RuntimeError("endpoint error")

    request = _FakeRequest({"Authorization": f"Bearer {token}"})
    with pytest.raises(RuntimeError, match="endpoint error"):
        await tenant_middleware(request, _fake_call_next)

    # 即使异常，上下文也应被清除
    assert get_tenant_context() is None
