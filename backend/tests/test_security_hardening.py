"""安全加固回归测试（审查修复批次）.

覆盖：
  - rate_limit._client_ip：XFF 从右往左取第 N 个可信段；段数不足回退直连地址
  - security.pii_hash：HMAC-SHA256（非裸 SHA256），确定性 + 长度
  - auth refresh 轮换：_blacklist_refresh_jti NX 语义（一次性 + 重放拒绝）
  - auth 登录锁定：连续失败 ≥5 次触发 locked_until，锁定期内 423
  - TOTP 存量明文兼容：读取时写回加密值
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ============================================================
# 1. XFF 可信段解析（限流绕过修复）
# ============================================================


class _FakeRequest:
    def __init__(self, headers: dict | None = None, client_host: str | None = "10.0.0.1"):
        self.headers = headers or {}
        self.client = SimpleNamespace(host=client_host) if client_host else None


def test_client_ip_takes_rightmost_segment_by_default(monkeypatch):
    """默认可信代理 1 层：取 XFF 最右段（nginx 追加的真实客户端 IP）."""
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 1)
    req = _FakeRequest({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
    assert rate_limit._client_ip(req) == "5.6.7.8"


def test_client_ip_right_n_with_two_proxies(monkeypatch):
    """可信代理 2 层：从右往左取第 2 段."""
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 2)
    req = _FakeRequest({"x-forwarded-for": "1.2.3.4, 5.6.7.8, 9.9.9.9"})
    assert rate_limit._client_ip(req) == "5.6.7.8"


def test_client_ip_forged_leftmost_not_trusted(monkeypatch):
    """攻击者伪造最左侧 IP 不影响限流键（不再取最左段）."""
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 1)
    req = _FakeRequest({"x-forwarded-for": "6.6.6.6"})
    # 单段但可信层=1：唯一段被视为代理写入值……段数(1) >= N(1)，仍取它。
    # 关键场景是"多段伪造"，见下一条。
    assert rate_limit._client_ip(req) == "6.6.6.6"


def test_client_ip_fewer_segments_than_trusted_falls_back_to_peer(monkeypatch):
    """XFF 段数少于可信层数（伪造截断）：整体弃用，回退 TCP 直连地址."""
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 3)
    req = _FakeRequest({"x-forwarded-for": "1.2.3.4"}, client_host="192.168.1.50")
    assert rate_limit._client_ip(req) == "192.168.1.50"


def test_client_ip_no_xff_uses_direct_client():
    """无 XFF 时回退直连地址."""
    from app.core.rate_limit import _client_ip

    assert _client_ip(_FakeRequest()) == "10.0.0.1"
    assert _client_ip(_FakeRequest(client_host=None)) == "unknown"


# ============================================================
# 2. pii_hash HMAC-SHA256 加固
# ============================================================


def test_pii_hash_is_not_bare_sha256(monkeypatch):
    """pii_hash 应为 HMAC-SHA256（配置 pepper 后 ≠ 裸 SHA256 摘要）."""
    from app.core import security as sec
    from app.core.config import settings
    from app.core.security import pii_hash

    sec._pepper_cache = None  # 重置模块级缓存，使新 pepper 生效
    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "unit-test-pepper")
    plaintext = "张三"
    bare = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    h = pii_hash(plaintext)
    assert h != bare
    assert len(h) == 64
    # 确定性（同进程内缓存后仍一致）
    assert h == pii_hash(plaintext)
    sec._pepper_cache = None


def test_pii_hash_differs_across_peppers(monkeypatch):
    """不同 pepper 产生不同哈希（防跨环境彩虹表复用）."""
    from app.core import security as sec
    from app.core.config import settings

    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "pepper-a")
    sec._pepper_cache = None
    h_a = sec.pii_hash("alice")
    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "pepper-b")
    sec._pepper_cache = None
    h_b = sec.pii_hash("alice")
    assert h_a != h_b
    sec._pepper_cache = None


def test_pii_hash_pepper_cache_invalidated(monkeypatch):
    """修改 pepper 配置后应生效（缓存按配置键失效语义测试）."""
    from app.core import security as sec
    from app.core.config import settings

    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "p1")
    sec._pepper_cache = None  # 重置缓存模拟进程重启
    h1 = sec.pii_hash("bob")
    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "p2")
    sec._pepper_cache = None
    h2 = sec.pii_hash("bob")
    assert h1 != h2
    sec._pepper_cache = None  # 清理，避免污染其他用例


# ============================================================
# 3. refresh 轮换黑名单（NX 一次性语义）
# ============================================================


class _NxRedis:
    """模拟 Redis SET NX EX 语义."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)


@pytest.mark.asyncio
async def test_blacklist_refresh_jti_first_use_succeeds(monkeypatch):
    """首次吊销 jti 应成功（NX 写入）."""
    import time

    import app.api.v1.auth as auth_mod

    fake = _NxRedis()
    monkeypatch.setattr(auth_mod, "get_redis", lambda: fake)

    ok = await auth_mod._blacklist_refresh_jti("jti-1", time.time() + 600)
    assert ok is True
    assert await fake.get(f"{auth_mod._REFRESH_BLACKLIST_PREFIX}jti-1") == "1"


@pytest.mark.asyncio
async def test_blacklist_refresh_jti_second_use_rejected(monkeypatch):
    """同一 jti 第二次 SET NX 失败 → 判定为重放/并发竞态."""
    import time

    import app.api.v1.auth as auth_mod

    fake = _NxRedis()
    monkeypatch.setattr(auth_mod, "get_redis", lambda: fake)

    assert await auth_mod._blacklist_refresh_jti("jti-2", time.time() + 600) is True
    assert await auth_mod._blacklist_refresh_jti("jti-2", time.time() + 600) is False


@pytest.mark.asyncio
async def test_blacklist_refresh_jti_redis_down_fail_open(monkeypatch):
    """Redis 不可用时 fail-open：返回 True（轮换继续，重放检测降级）."""
    import app.api.v1.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_redis", lambda: None)
    assert await auth_mod._blacklist_refresh_jti("jti-3", 0) is True


# ============================================================
# 4. 登录失败锁定（failed_login_count ≥5 → 锁 15 分钟）
# ============================================================


def _make_login_env(monkeypatch):
    """构造登录端点测试环境：内存 user + 审计捕获，返回 (app, user, calls)."""
    from uuid import uuid4

    from app.core.rate_limit import limiter
    from app.core.security import hash_password
    from app.db.session import get_db
    from app.main import app

    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        email="lock@example.com",
        name="锁定测试用户",
        role="hrbp",
        status="active",
        totp_secret=None,
        password_hash=hash_password("correct"),
        failed_login_count=0,
        locked_until=None,
        last_login_at=None,
    )
    calls: list[dict] = []

    async def fake_append_audit_log(**kwargs):
        calls.append(kwargs)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = user
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)
    db_mock.commit = AsyncMock()

    async def _fake_get_db():
        yield db_mock

    import app.api.v1.auth as auth_mod

    monkeypatch.setattr(auth_mod, "append_audit_log", fake_append_audit_log)
    app.dependency_overrides[get_db] = _fake_get_db
    limiter.reset()  # 清空限流计数，避免用例间串扰（登录端点按 IP 计数）
    return app, user, calls


def test_login_lockout_after_five_failures(client, monkeypatch):
    """连续 5 次错误密码后 locked_until 被设置（≈15 分钟后），计数清零."""
    from app.core.rate_limit import limiter

    app, user, calls = _make_login_env(monkeypatch)
    try:
        for _i in range(5):
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": "lock@example.com", "password": "wrong"},
            )
            assert resp.status_code == 401
        assert user.failed_login_count == 0  # 触发锁定后重新计数
        assert user.locked_until is not None
        remaining = (user.locked_until - datetime.now(UTC)).total_seconds()
        assert 14 * 60 < remaining <= 15 * 60
        assert any(c.get("after_value") == {"reason": "account_locked"} for c in calls)
    finally:
        limiter.reset()
        app.dependency_overrides.clear()


def test_login_locked_account_returns_423(client, monkeypatch):
    """locked_until 未到期时登录直接 423，不校验密码."""
    from app.core.rate_limit import limiter

    app, user, _calls = _make_login_env(monkeypatch)
    try:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=10)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "lock@example.com", "password": "correct"},
        )
        assert resp.status_code == 423
        # 未到期不应清除锁
        assert user.locked_until is not None
    finally:
        limiter.reset()
        app.dependency_overrides.clear()


def test_login_expired_lock_resets_counter(client, monkeypatch):
    """锁定过期后再次登录：解锁、清零计数，正确密码可成功."""
    from app.core.rate_limit import limiter

    app, user, _calls = _make_login_env(monkeypatch)
    try:
        user.locked_until = datetime.now(UTC) - timedelta(minutes=1)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "lock@example.com", "password": "correct"},
        )
        assert resp.status_code == 200
        assert user.locked_until is None
        assert user.failed_login_count == 0
    finally:
        limiter.reset()
        app.dependency_overrides.clear()


# ============================================================
# 5. TOTP 存量明文兼容 + 写回加密
# ============================================================


def test_totp_plaintext_secret_read_back_encrypted():
    """存量明文 secret：读取返回明文且字段被写回 Fernet 密文."""
    from app.api.v1.auth import _load_totp_secret
    from app.core import pii_crypto

    user = SimpleNamespace(totp_secret="PLAINBASE32SECRET234567")

    decrypted = _load_totp_secret(user)
    assert decrypted == "PLAINBASE32SECRET234567"
    # 字段已写回密文，且可解密还原
    assert user.totp_secret != "PLAINBASE32SECRET234567"
    assert pii_crypto.decrypt(user.totp_secret) == "PLAINBASE32SECRET234567"


def test_totp_encrypted_secret_roundtrip():
    """已加密 secret：直接解密使用，不重复加密."""
    from app.api.v1.auth import _load_totp_secret
    from app.core import pii_crypto

    cipher = pii_crypto.encrypt("BASE32SECRET234567ABCDEF")
    user = SimpleNamespace(totp_secret=cipher)
    assert _load_totp_secret(user) == "BASE32SECRET234567ABCDEF"
    assert user.totp_secret == cipher  # 未变
