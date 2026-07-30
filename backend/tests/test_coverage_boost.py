"""覆盖率提升测试 - 补充 security / deps / kill_switch / redis / shap / feature / main / llm 等.

针对低覆盖模块补充测试，目标覆盖率 ≥ 80%。所有代码用中文注释。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_pii,
    encrypt_pii,
    hash_password,
    pii_hash,
    verify_password,
)


# ============================================================
# 1. core/security.py 测试（覆盖 encrypt/decrypt/hash/JWT 分支）
# ============================================================


def test_hash_password_and_verify_password_roundtrip():
    """hash_password 应能被 verify_password 验证通过."""
    pwd = "S3crtP@ss"
    hashed = hash_password(pwd)
    assert hashed != pwd
    assert verify_password(pwd, hashed) is True


def test_verify_password_wrong_password_returns_false():
    """错误密码应返回 False."""
    assert verify_password("wrong-pwd", "$2b$12$hashedvalue") is False


def test_create_access_token_with_extra_payload():
    """create_access_token 应合并 extra 字段到 payload."""
    user_id = str(uuid4())
    tenant_id = str(uuid4())
    token = create_access_token(
        user_id, tenant_id, "admin", extra={"department": "HR", "scope": "read"}
    )
    payload = decode_token(token)
    assert payload["sub"] == user_id
    assert payload["tenant_id"] == tenant_id
    assert payload["role"] == "admin"
    assert payload["type"] == "access"
    assert payload["department"] == "HR"
    assert payload["scope"] == "read"
    assert "exp" in payload
    assert "iat" in payload


def test_create_refresh_token_decodes_correctly():
    """create_refresh_token 应生成 type=refresh 的 JWT."""
    user_id = str(uuid4())
    tenant_id = str(uuid4())
    token = create_refresh_token(user_id, tenant_id)
    payload = decode_token(token)
    assert payload["sub"] == user_id
    assert payload["tenant_id"] == tenant_id
    assert payload["type"] == "refresh"
    assert "exp" in payload


def test_encrypt_pii_returns_none_for_none_input():
    """encrypt_pii(None) 应返回 None."""
    assert encrypt_pii(None) is None


def test_decrypt_pii_returns_none_for_none_input():
    """decrypt_pii(None) 应返回 None."""
    assert decrypt_pii(None) is None


def test_encrypt_and_decrypt_pii_roundtrip():
    """encrypt_pii + decrypt_pii 应可往返."""
    plaintext = "张三的敏感信息-13800138000"
    ciphertext = encrypt_pii(plaintext)
    assert ciphertext != plaintext
    assert decrypt_pii(ciphertext) == plaintext


def test_decrypt_pii_invalid_token_returns_none():
    """decrypt_pii 非法 token 应返回 None（不抛异常）."""
    assert decrypt_pii("not-a-valid-fernet-token") is None


def test_decrypt_pii_invalid_bytes_returns_none():
    """decrypt_pii 解码失败（ValueError）应返回 None."""
    # 一个看起来像 Fernet token 但内容非法的字符串
    assert decrypt_pii("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=") is None


def test_pii_hash_returns_none_for_none_input():
    """pii_hash(None) 应返回 None."""
    assert pii_hash(None) is None


def test_pii_hash_deterministic_and_hex():
    """pii_hash 应是确定性的 SHA256 hex."""
    h1 = pii_hash("张三")
    h2 = pii_hash("张三")
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex 长度
    # 不同输入产生不同哈希
    assert pii_hash("张三") != pii_hash("李四")


# ============================================================
# 2. api/deps.py 测试（覆盖 get_current_user 全部分支 + require_role）
# ============================================================


@pytest.mark.asyncio
async def test_get_current_user_missing_authorization_returns_401():
    """无 Authorization 头应抛 401."""
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization=None, db=MagicMock())
    assert exc_info.value.status_code == 401
    assert "Bearer" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_non_bearer_prefix_returns_401():
    """Authorization 非 Bearer 开头应抛 401."""
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization="Basic abc123", db=MagicMock())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_invalid_token_returns_401():
    """无效 JWT 应抛 401."""
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization="Bearer invalid.token.here", db=MagicMock())
    assert exc_info.value.status_code == 401
    assert "JWT" in exc_info.value.detail or "解码" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_wrong_token_type_returns_401():
    """token type != access 应抛 401."""
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    user_id = str(uuid4())
    tenant_id = str(uuid4())
    refresh_token = create_refresh_token(user_id, tenant_id)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization=f"Bearer {refresh_token}", db=MagicMock())
    assert exc_info.value.status_code == 401
    assert "类型" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_user_not_found_returns_401():
    """用户不存在应抛 401."""
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    user_id = str(uuid4())
    tenant_id = str(uuid4())
    token = create_access_token(user_id, tenant_id, "hr")

    # mock db.execute 返回 None（用户不存在）
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401
    assert "不存在" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_user_inactive_returns_403():
    """用户 status != active 应抛 403."""
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    user_id = str(uuid4())
    tenant_id = str(uuid4())
    token = create_access_token(user_id, tenant_id, "hr")

    fake_user = MagicMock()
    fake_user.status = "disabled"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_user
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 403
    assert "禁用" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_success_returns_user():
    """正常流程应返回 user 对象."""
    from app.api.deps import get_current_user

    user_id = str(uuid4())
    tenant_id = str(uuid4())
    token = create_access_token(user_id, tenant_id, "hr")

    fake_user = MagicMock()
    fake_user.status = "active"
    fake_user.role = "hr"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_user
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    user = await get_current_user(authorization=f"Bearer {token}", db=db)
    assert user is fake_user


@pytest.mark.asyncio
async def test_require_role_allows_authorized_role():
    """require_role 应允许授权角色通过."""
    from app.api.deps import require_role

    fake_user = MagicMock()
    fake_user.role = "admin"

    guard = require_role("admin", "hr_manager")
    # _guard 依赖 get_current_user，但这里直接传 user 参数绕过依赖
    result = await guard(user=fake_user)
    assert result is fake_user


@pytest.mark.asyncio
async def test_require_role_denies_unauthorized_role():
    """require_role 应拒绝未授权角色."""
    from fastapi import HTTPException

    from app.api.deps import require_role

    fake_user = MagicMock()
    fake_user.role = "employee"

    guard = require_role("admin", "hr_manager")
    with pytest.raises(HTTPException) as exc_info:
        await guard(user=fake_user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_role_empty_roles_allows_any_user():
    """require_role() 无参数时应允许任何角色（无限制）."""
    from app.api.deps import require_role

    fake_user = MagicMock()
    fake_user.role = "employee"

    guard = require_role()
    result = await guard(user=fake_user)
    assert result is fake_user


def test_get_tenant_id_from_context_returns_uuid(monkeypatch):
    """get_tenant_id_from_context 应返回 UUID 形式的 tenant_id."""
    import app.api.deps as deps_mod

    tid = uuid4()
    # deps_mod 已从 tenant_mod 导入 get_current_tenant_id，需在 deps_mod 命名空间 patch
    monkeypatch.setattr(deps_mod, "get_current_tenant_id", lambda: str(tid))

    result = deps_mod.get_tenant_id_from_context()
    assert result == tid


# ============================================================
# 3. core/kill_switch.py 测试（覆盖 _build_payload / _parse_status / 异常分支）
# ============================================================


def test_kill_switch_build_payload_active_true():
    """_build_payload(active=True) 应含 active=1."""
    from app.core import kill_switch

    payload = kill_switch._build_payload(True, "test reason", "op-001")
    assert payload["active"] == "1"
    assert payload["reason"] == "test reason"
    assert payload["activated_by"] == "op-001"
    assert payload["activated_at"] != ""


def test_kill_switch_build_payload_active_false():
    """_build_payload(active=False) 应含 active=0."""
    from app.core import kill_switch

    payload = kill_switch._build_payload(False, "", "op-002")
    assert payload["active"] == "0"


def test_kill_switch_parse_status_none_returns_default():
    """_parse_status(None) 应返回默认 inactive dict."""
    from app.core import kill_switch

    result = kill_switch._parse_status(None)
    assert result == {
        "active": False, "reason": "", "activated_at": "", "activated_by": "",
    }


def test_kill_switch_parse_status_empty_string_returns_default():
    """_parse_status('') 应返回默认 inactive dict."""
    from app.core import kill_switch

    result = kill_switch._parse_status("")
    assert result["active"] is False


def test_kill_switch_parse_status_invalid_json_returns_default():
    """_parse_status 非法 JSON 应返回默认 dict."""
    from app.core import kill_switch

    result = kill_switch._parse_status("not-json")
    assert result["active"] is False
    assert result["reason"] == ""


def test_kill_switch_parse_status_valid_json_active():
    """_parse_status 有效 JSON active=1 应返回 active=True."""
    from app.core import kill_switch

    raw = json.dumps({
        "active": "1", "reason": "drift", "activated_at": "2026-01-01", "activated_by": "admin"
    })
    result = kill_switch._parse_status(raw)
    assert result["active"] is True
    assert result["reason"] == "drift"
    assert result["activated_by"] == "admin"


def test_kill_switch_parse_status_active_zero_returns_false():
    """active=0 应解析为 False."""
    from app.core import kill_switch

    raw = json.dumps({"active": "0"})
    result = kill_switch._parse_status(raw)
    assert result["active"] is False


def test_kill_switch_get_sync_redis_failure_returns_none(monkeypatch):
    """_get_sync_redis 连接失败应返回 None."""
    from app.core import kill_switch

    # 重置单例
    kill_switch._reset_sync_singleton()

    # mock redis.from_url 抛异常
    import redis as sync_redis_lib

    def _fake_from_url(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(sync_redis_lib, "from_url", _fake_from_url)

    client = kill_switch._get_sync_redis()
    assert client is None
    # 再次调用应走 None 分支（已被设为 None 缓存）
    # 但由于内部已 None，下次会再次尝试；先重置再测
    kill_switch._reset_sync_singleton()


def test_kill_switch_sync_is_active_redis_exception_returns_false(monkeypatch):
    """同步 is_active 在 Redis 异常时应返回 False（fail-open）."""
    from app.core import kill_switch

    # mock _get_sync_redis 返回一个会抛异常的 client
    fake_client = MagicMock()
    fake_client.get = MagicMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(kill_switch, "_get_sync_redis", lambda: fake_client)

    assert kill_switch.is_active() is False


def test_kill_switch_sync_activate_redis_exception_logs_error(monkeypatch):
    """同步 activate 在 Redis 异常时应仅 log，不抛."""
    from app.core import kill_switch

    fake_client = MagicMock()
    fake_client.set = MagicMock(side_effect=RuntimeError("write fail"))
    monkeypatch.setattr(kill_switch, "_get_sync_redis", lambda: fake_client)

    # 不抛异常
    kill_switch.activate(reason="test", operator_id="op")
    fake_client.set.assert_called_once()


def test_kill_switch_sync_deactivate_redis_exception_logs_error(monkeypatch):
    """同步 deactivate 在 Redis 异常时应仅 log，不抛."""
    from app.core import kill_switch

    fake_client = MagicMock()
    fake_client.delete = MagicMock(side_effect=RuntimeError("delete fail"))
    monkeypatch.setattr(kill_switch, "_get_sync_redis", lambda: fake_client)

    kill_switch.deactivate(operator_id="op")
    fake_client.delete.assert_called_once()


def test_kill_switch_sync_get_status_redis_exception_returns_default(monkeypatch):
    """get_status Redis 异常时应返回默认 inactive dict."""
    from app.core import kill_switch

    fake_client = MagicMock()
    fake_client.get = MagicMock(side_effect=RuntimeError("read fail"))
    monkeypatch.setattr(kill_switch, "_get_sync_redis", lambda: fake_client)

    status = kill_switch.get_status()
    assert status["active"] is False


@pytest.mark.asyncio
async def test_kill_switch_async_is_active_redis_exception_returns_false(monkeypatch):
    """异步 is_active_async 在 Redis 异常时应返回 False."""
    from app.core import kill_switch

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(side_effect=RuntimeError("async redis down"))
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake_redis)

    assert await kill_switch.is_active_async() is False


@pytest.mark.asyncio
async def test_kill_switch_async_is_active_redis_none_returns_false(monkeypatch):
    """异步 is_active_async 在 Redis 不可用（None）时应返回 False."""
    from app.core import kill_switch

    monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
    assert await kill_switch.is_active_async() is False


@pytest.mark.asyncio
async def test_kill_switch_async_activate_redis_exception(monkeypatch):
    """异步 activate 在 Redis 异常时应仅 log，不抛."""
    from app.core import kill_switch

    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(side_effect=RuntimeError("async write fail"))
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake_redis)

    await kill_switch.activate_async(reason="test", operator_id="op")
    fake_redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_async_activate_redis_none(monkeypatch):
    """异步 activate 在 Redis None 时应仅 log，不抛."""
    from app.core import kill_switch

    monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
    await kill_switch.activate_async(reason="test", operator_id="op")


@pytest.mark.asyncio
async def test_kill_switch_async_deactivate_redis_exception(monkeypatch):
    """异步 deactivate 在 Redis 异常时应仅 log，不抛."""
    from app.core import kill_switch

    fake_redis = AsyncMock()
    fake_redis.delete = AsyncMock(side_effect=RuntimeError("async delete fail"))
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake_redis)

    await kill_switch.deactivate_async(operator_id="op")
    fake_redis.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_async_deactivate_redis_none(monkeypatch):
    """异步 deactivate 在 Redis None 时应仅 log，不抛."""
    from app.core import kill_switch

    monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
    await kill_switch.deactivate_async(operator_id="op")


@pytest.mark.asyncio
async def test_kill_switch_async_get_status_redis_exception(monkeypatch):
    """异步 get_status 在 Redis 异常时应返回默认 inactive dict."""
    from app.core import kill_switch

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(side_effect=RuntimeError("async read fail"))
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake_redis)

    status = await kill_switch.get_status_async()
    assert status["active"] is False


@pytest.mark.asyncio
async def test_kill_switch_async_get_status_redis_none(monkeypatch):
    """异步 get_status 在 Redis None 时应返回默认 inactive dict."""
    from app.core import kill_switch

    monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
    status = await kill_switch.get_status_async()
    assert status["active"] is False


# ============================================================
# 4. core/redis.py 测试（覆盖 init/close/get_redis 分支）
# ============================================================


@pytest.mark.asyncio
async def test_init_redis_idempotent_when_already_initialized(monkeypatch):
    """init_redis 在已初始化时应是幂等的（直接 return）."""
    import app.core.redis as redis_mod

    # 预设 _redis_client
    existing = MagicMock()
    monkeypatch.setattr(redis_mod, "_redis_client", existing)

    # 不应调用 aioredis.from_url
    await redis_mod.init_redis()

    # _redis_client 仍是原对象
    assert redis_mod._redis_client is existing


@pytest.mark.asyncio
async def test_init_redis_connection_failure_sets_none(monkeypatch):
    """init_redis 连接失败应将 _redis_client 设为 None（不抛异常）."""
    import app.core.redis as redis_mod
    import redis.asyncio as aioredis

    # 重置单例
    monkeypatch.setattr(redis_mod, "_redis_client", None)

    def _fake_from_url(*args, **kwargs):
        fake_client = AsyncMock()
        fake_client.ping = AsyncMock(side_effect=RuntimeError("conn refused"))
        return fake_client

    monkeypatch.setattr(aioredis, "from_url", _fake_from_url)

    # 不应抛异常
    await redis_mod.init_redis()
    assert redis_mod._redis_client is None


@pytest.mark.asyncio
async def test_close_redis_when_client_none_is_noop(monkeypatch):
    """close_redis 在 _redis_client=None 时应是 no-op."""
    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "_redis_client", None)
    # 不抛异常
    await redis_mod.close_redis()
    assert redis_mod._redis_client is None


@pytest.mark.asyncio
async def test_close_redis_with_exception_clears_client(monkeypatch):
    """close_redis 在 close() 异常时应 finally 清空 _redis_client."""
    import app.core.redis as redis_mod

    fake_client = AsyncMock()
    fake_client.close = AsyncMock(side_effect=RuntimeError("close fail"))
    monkeypatch.setattr(redis_mod, "_redis_client", fake_client)

    # 不抛异常（被 try/except 捕获）
    await redis_mod.close_redis()
    # finally 块应清空单例
    assert redis_mod._redis_client is None


@pytest.mark.asyncio
async def test_close_redis_success_clears_client(monkeypatch):
    """close_redis 正常关闭应清空 _redis_client."""
    import app.core.redis as redis_mod

    fake_client = AsyncMock()
    fake_client.close = AsyncMock()
    monkeypatch.setattr(redis_mod, "_redis_client", fake_client)

    await redis_mod.close_redis()
    assert redis_mod._redis_client is None


def test_get_redis_returns_current_client():
    """get_redis 应返回当前 _redis_client."""
    import app.core.redis as redis_mod

    fake = MagicMock()
    redis_mod._redis_client = fake
    try:
        assert redis_mod.get_redis() is fake
    finally:
        redis_mod._redis_client = None


# ============================================================
# 5. main.py 测试（覆盖 lifespan）
# ============================================================


@pytest.mark.asyncio
async def test_lifespan_initializes_and_closes_redis(monkeypatch):
    """lifespan 应调用 init_redis / close_redis（即使失败也不抛）."""
    from app.main import lifespan

    init_called = False
    close_called = False

    async def _fake_init():
        nonlocal init_called
        init_called = True

    async def _fake_close():
        nonlocal close_called
        close_called = True

    import app.main as main_mod
    monkeypatch.setattr(main_mod, "init_redis", _fake_init)
    monkeypatch.setattr(main_mod, "close_redis", _fake_close)

    async with lifespan(MagicMock()):
        pass

    assert init_called
    assert close_called


@pytest.mark.asyncio
async def test_lifespan_init_exception_does_not_block(monkeypatch):
    """init_redis 异常不应阻塞 lifespan."""
    from app.main import lifespan

    async def _fake_init():
        raise RuntimeError("init failed")

    async def _fake_close():
        pass

    import app.main as main_mod
    monkeypatch.setattr(main_mod, "init_redis", _fake_init)
    monkeypatch.setattr(main_mod, "close_redis", _fake_close)

    # 不应抛异常
    async with lifespan(MagicMock()):
        pass


@pytest.mark.asyncio
async def test_lifespan_close_exception_does_not_block(monkeypatch):
    """close_redis 异常不应阻塞 lifespan."""
    from app.main import lifespan

    async def _fake_init():
        pass

    async def _fake_close():
        raise RuntimeError("close failed")

    import app.main as main_mod
    monkeypatch.setattr(main_mod, "init_redis", _fake_init)
    monkeypatch.setattr(main_mod, "close_redis", _fake_close)

    # 不应抛异常
    async with lifespan(MagicMock()):
        pass


# ============================================================
# 6. ml/shap_explainer.py 测试（覆盖 _to_array 分支）
# ============================================================


def test_shap_to_array_with_list_input():
    """_to_array 对 list 输入应取最后一个元素（正类）."""
    from app.ml.shap_explainer import ShapExplainer

    explainer = ShapExplainer.__new__(ShapExplainer)  # 跳过 __init__
    arr1 = np.array([[0.1, 0.2]])
    arr2 = np.array([[0.3, 0.4]])
    result = explainer._to_array([arr1, arr2])
    # 应取最后一个元素
    np.testing.assert_array_equal(result, arr2)


def test_shap_to_array_with_3d_array_takes_positive_class():
    """_to_array 对 3D 数组应取正类（最后一维）."""
    from app.ml.shap_explainer import ShapExplainer

    explainer = ShapExplainer.__new__(ShapExplainer)
    arr = np.array([[[0.1, 0.2], [0.3, 0.4]]])  # shape (1, 2, 2)
    result = explainer._to_array(arr)
    # 应取 arr[:, :, -1]
    np.testing.assert_array_equal(result, np.array([[0.2, 0.4]]))


def test_shap_to_array_with_2d_array_returns_as_is():
    """_to_array 对 2D 数组应原样返回."""
    from app.ml.shap_explainer import ShapExplainer

    explainer = ShapExplainer.__new__(ShapExplainer)
    arr = np.array([[0.1, 0.2, 0.3]])
    result = explainer._to_array(arr)
    np.testing.assert_array_equal(result, arr)


# ============================================================
# 7. ml/feature_engineering.py 测试（覆盖 _safe_col_name + build_features）
# ============================================================


def test_safe_col_name_replaces_ampersand_and_space():
    """_safe_col_name 应将 & 替换为空字符串，空格替换为下划线."""
    from app.ml.feature_engineering import _safe_col_name

    assert _safe_col_name("R&D") == "RD"
    assert _safe_col_name("Sales Dept") == "Sales_Dept"
    assert _safe_col_name("HR") == "HR"
    assert _safe_col_name("R & D") == "R__D"


def test_build_features_writes_processed_files(tmp_path, monkeypatch):
    """build_features 应写入 processed/ 与 models/ 目录."""
    import app.ml.feature_engineering as fe_mod
    from app.ml.data_generation import generate_all

    # 生成少量 raw 数据到 tmp_path
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    structured, behavior = generate_all(n=30, seed=42)
    structured.to_csv(raw_dir / "structured_train.csv", index=False)
    behavior.to_csv(raw_dir / "behavior_train.csv", index=False)

    # patch 路径常量指向 tmp_path
    monkeypatch.setattr(fe_mod, "RAW_DIR", raw_dir)
    monkeypatch.setattr(fe_mod, "PROCESSED_DIR", tmp_path / "processed")
    monkeypatch.setattr(fe_mod, "MODELS_DIR", tmp_path / "models")

    splits = fe_mod.build_features()

    # 应返回含划分数据的 dict
    assert "X_struct_train" in splits
    assert "X_struct_val" in splits
    assert "X_struct_test" in splits
    assert "y_train" in splits
    assert "y_test" in splits
    assert "audit_test" in splits

    # processed/ 与 models/ 目录应被创建
    assert (tmp_path / "processed").exists()
    assert (tmp_path / "models").exists()
    # metadata 文件应存在
    assert (tmp_path / "models" / "feature_metadata.pkl").exists()
    # 划分 CSV 应存在
    assert (tmp_path / "processed" / "X_struct_train.csv").exists()
    assert (tmp_path / "processed" / "split_indices.npz").exists()


# ============================================================
# 8. services/llm_service.py 测试（覆盖 stream_advice 备用链 + _call_dashscope_sse httpx mock）
# ============================================================


@pytest.mark.asyncio
async def test_stream_advice_falls_back_to_secondary_llm_when_primary_fails(monkeypatch):
    """主 LLM 失败 + LLM_FALLBACK 配置时应尝试备用 LLM（也失败则降级模板）."""
    from app.core.config import settings
    from app.services.llm_service import LLMService

    # 启用备用 LLM
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "LLM_PRIMARY", "qwen-max")
    monkeypatch.setattr(settings, "LLM_FALLBACK", "deepseek-v3")

    call_count = {"n": 0}

    @classmethod
    async def _fake_call(cls, prompt, model):
        call_count["n"] += 1
        # 主模型与备用模型都失败
        raise RuntimeError(f"model {model} failed")
        yield  # 让 Python 把这函数识别为 async generator（不会执行到此行）

    monkeypatch.setattr(LLMService, "_call_dashscope_sse", _fake_call)

    chunks = []
    async for chunk in LLMService.stream_advice(
        {"name": "员工A"}, [{"feature": "Age", "contribution": 0.3}], risk_score=70
    ):
        chunks.append(chunk)

    # 应调用 2 次（主 + 备用）
    assert call_count["n"] == 2
    # 应降级到模板
    chunk_types = [list(c.keys())[0] for c in chunks]
    assert "chunk" in chunk_types
    assert chunks[-1] == {"done": True}


@pytest.mark.asyncio
async def test_stream_advice_secondary_llm_succeeds(monkeypatch):
    """主 LLM 失败但备用 LLM 成功时应使用备用 LLM 输出."""
    from app.core.config import settings
    from app.services.llm_service import LLMService

    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "LLM_PRIMARY", "qwen-max")
    monkeypatch.setattr(settings, "LLM_FALLBACK", "deepseek-v3")

    @classmethod
    async def _fake_call(cls, prompt, model):
        if model == "qwen-max":
            raise RuntimeError("primary failed")
        # 备用模型成功
        yield {"chunk": "备用建议"}
        yield {"done": True}

    monkeypatch.setattr(LLMService, "_call_dashscope_sse", _fake_call)

    chunks = []
    async for chunk in LLMService.stream_advice(
        {"name": "员工A"}, [], risk_score=60
    ):
        chunks.append(chunk)

    # 应含备用 LLM 的输出
    assert any(c.get("chunk") == "备用建议" for c in chunks)
    assert chunks[-1] == {"done": True}


@pytest.mark.asyncio
async def test_stream_advice_primary_succeeds_uses_primary(monkeypatch):
    """主 LLM 成功时应直接使用主 LLM 输出."""
    from app.core.config import settings
    from app.services.llm_service import LLMService

    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "LLM_PRIMARY", "qwen-max")
    monkeypatch.setattr(settings, "LLM_FALLBACK", "deepseek-v3")

    @classmethod
    async def _fake_call(cls, prompt, model):
        assert model == "qwen-max"
        yield {"chunk": "主模型建议"}
        yield {"done": True}

    monkeypatch.setattr(LLMService, "_call_dashscope_sse", _fake_call)

    chunks = []
    async for chunk in LLMService.stream_advice(
        {"name": "员工A"}, [], risk_score=60
    ):
        chunks.append(chunk)

    assert any(c.get("chunk") == "主模型建议" for c in chunks)


@pytest.mark.asyncio
async def test_call_dashscope_sse_parses_sse_lines(monkeypatch):
    """_call_dashscope_sse 应解析 SSE data: 行并 yield chunk."""
    from app.core.config import settings
    from app.services.llm_service import LLMService

    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "fake-key")

    # 构造 SSE 响应行
    sse_lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": " world"}}]}),
        "data: [DONE]",
    ]

    class _FakeResponse:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    class _FakeStream:
        def __init__(self):
            self._resp = _FakeResponse()

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *args):
            pass

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return _FakeStream()

    # 用 patch 替换整个 AsyncClient 类（避免内部状态校验）
    import app.services.llm_service as llm_mod
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", _FakeAsyncClient)

    chunks = []
    async for chunk in LLMService._call_dashscope_sse("test prompt", "qwen-max"):
        chunks.append(chunk)

    # 应有 chunk + done
    assert any(c.get("chunk") == "Hello" for c in chunks)
    assert any(c.get("chunk") == " world" for c in chunks)
    assert chunks[-1] == {"done": True}


@pytest.mark.asyncio
async def test_call_dashscope_sse_skips_invalid_lines(monkeypatch):
    """_call_dashscope_sse 应跳过空行/非 data 行/无效 JSON."""
    from app.core.config import settings
    from app.services.llm_service import LLMService

    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "fake-key")

    sse_lines = [
        "",  # 空行
        ": comment",  # 注释行
        "data: not-valid-json",  # 无效 JSON
        "data: " + json.dumps({"choices": []}),  # 空 choices
        "data: " + json.dumps({"choices": [{"delta": {}}]}),  # 无 content
        "data: " + json.dumps({"choices": [{"delta": {"content": "valid"}}]}),
        "data: [DONE]",
    ]

    class _FakeResponse:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    class _FakeStream:
        def __init__(self):
            self._resp = _FakeResponse()

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *args):
            pass

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return _FakeStream()

    import app.services.llm_service as llm_mod
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", _FakeAsyncClient)

    chunks = []
    async for chunk in LLMService._call_dashscope_sse("test", "qwen-max"):
        chunks.append(chunk)

    # 应仅有 valid chunk + done
    chunk_only = [c for c in chunks if "chunk" in c]
    assert len(chunk_only) == 1
    assert chunk_only[0]["chunk"] == "valid"
    assert chunks[-1] == {"done": True}


# ============================================================
# 9. api/v1/ws.py 测试（覆盖 _add_connection / _remove_connection / broadcast）
# ============================================================


def test_ws_add_connection_registers_websocket():
    """_add_connection 应将 WebSocket 加入租户池."""
    from app.api.v1 import ws as ws_mod

    # 清空连接池
    ws_mod._connections.clear()

    fake_ws = MagicMock()
    ws_mod._add_connection("tenant-1", fake_ws)
    assert "tenant-1" in ws_mod._connections
    assert fake_ws in ws_mod._connections["tenant-1"]

    # 清理
    ws_mod._connections.clear()


def test_ws_remove_connection_removes_websocket():
    """_remove_connection 应移除指定 WebSocket."""
    from app.api.v1 import ws as ws_mod

    ws_mod._connections.clear()
    fake_ws = MagicMock()
    ws_mod._add_connection("tenant-1", fake_ws)

    ws_mod._remove_connection("tenant-1", fake_ws)
    assert "tenant-1" not in ws_mod._connections  # 空池应被清除


def test_ws_remove_connection_unknown_tenant_is_noop():
    """_remove_connection 对未知租户应是 no-op."""
    from app.api.v1 import ws as ws_mod

    ws_mod._connections.clear()
    # 不抛异常
    ws_mod._remove_connection("unknown-tenant", MagicMock())


def test_ws_remove_connection_unknown_websocket_is_noop():
    """_remove_connection 对未注册的 WebSocket 应是 no-op."""
    from app.api.v1 import ws as ws_mod

    ws_mod._connections.clear()
    fake_ws = MagicMock()
    ws_mod._add_connection("tenant-1", fake_ws)

    # 移除未注册的另一个 ws
    other_ws = MagicMock()
    ws_mod._remove_connection("tenant-1", other_ws)
    # tenant-1 池仍应存在（含 fake_ws）
    assert "tenant-1" in ws_mod._connections
    assert fake_ws in ws_mod._connections["tenant-1"]
    ws_mod._connections.clear()


@pytest.mark.asyncio
async def test_broadcast_risk_update_no_connections_is_noop():
    """无连接时 broadcast_risk_update 应静默跳过."""
    from app.api.v1 import ws as ws_mod

    ws_mod._connections.clear()
    # 不抛异常
    await ws_mod.broadcast_risk_update("tenant-empty", {"type": "test"})


@pytest.mark.asyncio
async def test_broadcast_risk_update_sends_to_all_connections():
    """broadcast_risk_update 应向所有连接发送消息."""
    from app.api.v1 import ws as ws_mod

    ws_mod._connections.clear()
    ws1 = MagicMock()
    ws1.send_text = AsyncMock()
    ws2 = MagicMock()
    ws2.send_text = AsyncMock()

    ws_mod._add_connection("tenant-1", ws1)
    ws_mod._add_connection("tenant-1", ws2)

    await ws_mod.broadcast_risk_update("tenant-1", {"type": "risk_update", "risk_score": 75})

    ws1.send_text.assert_awaited_once()
    ws2.send_text.assert_awaited_once()
    # payload 应是 JSON 字符串
    payload = ws1.send_text.await_args.args[0]
    assert "risk_update" in payload
    assert "75" in payload

    ws_mod._connections.clear()


@pytest.mark.asyncio
async def test_broadcast_risk_update_removes_dead_connections():
    """broadcast_risk_update 应移除发送失败的连接."""
    from app.api.v1 import ws as ws_mod

    ws_mod._connections.clear()
    dead_ws = MagicMock()
    dead_ws.send_text = AsyncMock(side_effect=RuntimeError("connection closed"))
    alive_ws = MagicMock()
    alive_ws.send_text = AsyncMock()

    ws_mod._add_connection("tenant-1", dead_ws)
    ws_mod._add_connection("tenant-1", alive_ws)

    await ws_mod.broadcast_risk_update("tenant-1", {"type": "test"})

    # dead_ws 应被移除，alive_ws 仍在
    assert "tenant-1" in ws_mod._connections
    assert alive_ws in ws_mod._connections["tenant-1"]
    assert dead_ws not in ws_mod._connections["tenant-1"]

    ws_mod._connections.clear()
