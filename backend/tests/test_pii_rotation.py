"""PII 多钥轮换测试（PII_PREVIOUS_KEYS 模式，对标 bysj）.

覆盖：
  - 主钥轮换后旧密文经 previous keys 回退解密成功（debug 标记 legacy_key_used）
  - 双旧钥按声明顺序回退
  - 篡改密文失败、无 previous 时行为不变
  - 加密/哈希派生始终用当前主钥；pepper 固定时 hash_field 跨钥稳定
"""
from __future__ import annotations

import inspect
import logging

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core import pii_crypto, security
from app.core.config import settings


def _gen_keys(n: int) -> list[str]:
    return [Fernet.generate_key().decode() for _ in range(n)]


@pytest.fixture(autouse=True)
def _clean_rotation_state(monkeypatch):
    """每例隔离：清空 previous keys / 显式 pepper，重置模块缓存."""
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", "", raising=False)
    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "", raising=False)
    security._pepper_cache = None
    yield
    security._pepper_cache = None


# ===== 1. 主钥轮换 → previous keys 回退解密 =====


def test_rotate_primary_key_old_token_decrypted_with_legacy_marker(
    monkeypatch, caplog
):
    """主钥加密 → 换新钥（旧钥进 PREVIOUS）→ 解密成功且日志标记 legacy."""
    k1, k2 = _gen_keys(2)
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k1)
    plaintext = "张三-身份证-110101199001011234"
    old_token = pii_crypto.encrypt(plaintext)

    # 轮换：新钥生效，旧钥进历史链
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k2)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", k1)

    with caplog.at_level(logging.DEBUG, logger="app.core.pii_crypto"):
        assert pii_crypto.decrypt(old_token) == plaintext
    legacy_logs = [r for r in caplog.records if "legacy_key_used=true" in r.message]
    assert len(legacy_logs) == 1


def test_new_token_after_rotation_uses_primary_not_previous(monkeypatch):
    """轮换后新加密必须用新主钥：旧钥单独无法解开新密文."""
    k1, k2 = _gen_keys(2)
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k1)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", "")
    plaintext = "13800138000"

    monkeypatch.setattr(settings, "PII_FERNET_KEY", k2)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", k1)
    new_token = pii_crypto.encrypt(plaintext)

    # 旧钥解不开新密文 → 新密文确实由主钥生成
    with pytest.raises(InvalidToken):
        Fernet(k1.encode()).decrypt(new_token.encode())
    assert pii_crypto.decrypt(new_token) == plaintext


def test_two_previous_keys_fallback_in_declared_order(monkeypatch, caplog):
    """双旧钥顺序回退：k2 命中记 index=0，k1 需等 k2 失败后命中记 index=1."""
    k1, k2, k3 = _gen_keys(3)
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k2)
    token_k2 = pii_crypto.encrypt("薪资-20000")
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k1)
    token_k1 = pii_crypto.encrypt("姓名-李四")

    # 当前主钥 k3，历史链声明顺序 k2 → k1
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k3)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", f"{k2},{k1}")

    with caplog.at_level(logging.DEBUG, logger="app.core.pii_crypto"):
        assert pii_crypto.decrypt(token_k2) == "薪资-20000"
        assert pii_crypto.decrypt(token_k1) == "姓名-李四"

    idxs = [
        int(r.getMessage().split("previous_key_index=")[1].split()[0])
        for r in caplog.records
        if "legacy_key_used=true" in r.getMessage()
    ]
    assert idxs == [0, 1]  # 先试 k2 后试 k1，顺序与声明一致


def test_duplicate_and_invalid_previous_segments_tolerated(monkeypatch, caplog):
    """PREVIOUS 列表容忍空段/空白/重复/非法段，合法旧钥仍可回退."""
    k1, k2 = _gen_keys(2)
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k1)
    token = pii_crypto.encrypt("民族-汉族")

    monkeypatch.setattr(settings, "PII_FERNET_KEY", k2)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", f" , {k1} ,,{k1},not-a-fernet-key")

    with caplog.at_level(logging.DEBUG, logger="app.core.pii_crypto"):
        assert pii_crypto.decrypt(token) == "民族-汉族"
    assert sum("legacy_key_used=true" in r.message for r in caplog.records) == 1
    # 非法段被跳过并告警，不影响整体解密链
    assert any("非法密钥段" in r.message for r in caplog.records)


# ===== 2. 失败路径与无轮换场景 =====


def test_tampered_ciphertext_fails_even_with_previous_keys(monkeypatch):
    """篡改密文即使配置了 previous keys 也必须失败（InvalidToken）."""
    k1, k2 = _gen_keys(2)
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k1)
    token = pii_crypto.encrypt("salary-secret")
    tampered = ("A" if token[-1] != "A" else "B") + token[1:] if token else token

    monkeypatch.setattr(settings, "PII_FERNET_KEY", k2)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", k1)
    with pytest.raises(InvalidToken):
        pii_crypto.decrypt(tampered)


def test_no_previous_keys_behavior_unchanged(monkeypatch):
    """未配置 PREVIOUS 时行为与旧版一致：本钥往返成功、他钥密文报 InvalidToken."""
    k_foreign, k_current = _gen_keys(2)
    foreign_token = Fernet(k_foreign.encode()).encrypt("外部密文".encode())

    monkeypatch.setattr(settings, "PII_FERNET_KEY", k_current)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", "")

    plain = "手机号-13900000000"
    assert pii_crypto.decrypt(pii_crypto.encrypt(plain)) == plain
    with pytest.raises(InvalidToken):
        pii_crypto.decrypt(foreign_token.decode())


# ===== 3. hash_field 与 pepper 派生链兼容 =====


def test_hash_field_stable_across_key_rotation_when_pepper_fixed(monkeypatch):
    """pepper 固定时 hash_field 跨 Fernet 密钥轮换稳定."""
    k1, k2 = _gen_keys(2)
    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "fixed-pepper-for-rotation")
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k1)
    h_before = pii_crypto.hash_field("王五")

    # 轮换主钥 + 挂载历史链，显式 pepper 不变
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k2)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", k1)
    security._pepper_cache = None  # 模拟进程重启使 pepper 重读

    assert pii_crypto.hash_field("王五") == h_before


def test_hash_field_derived_pepper_follows_primary_only(monkeypatch):
    """pepper 缺失走派生回退链时只用当前主钥：轮换后哈希随之更新，
    且不因 PREVIOUS_KEYS 引入旧派生结果（回退链语义不变）。"""
    k1, k2 = _gen_keys(2)
    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "")
    monkeypatch.setattr(settings, "PII_FERNET_KEY", k1)
    h_old = pii_crypto.hash_field("赵六")

    monkeypatch.setattr(settings, "PII_FERNET_KEY", k2)
    monkeypatch.setattr(settings, "PII_PREVIOUS_KEYS", k1)
    security._pepper_cache = None
    h_new = pii_crypto.hash_field("赵六")

    assert h_new != h_old
    assert h_new == security.pii_hash("赵六")  # 与 pii_hash 派生链同源


def test_hash_field_matches_pii_hash_format(monkeypatch):
    """hash_field 与 security.pii_hash 同源同格式（64 位十六进制 HMAC 摘要）."""
    monkeypatch.setattr(settings, "PII_HASH_PEPPER", "format-check")
    security._pepper_cache = None
    h = pii_crypto.hash_field("孙七")
    assert h == security.pii_hash("孙七")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    security._pepper_cache = None


# ===== 4. 对外签名兼容 =====


def test_public_signatures_unchanged():
    """既有对外函数签名不变，调用方零改动."""

    def _norm(sig_str: str) -> str:
        return sig_str.replace("'", "")

    assert _norm(str(inspect.signature(pii_crypto.encrypt))) == "(plaintext: str) -> str"
    assert (
        _norm(str(inspect.signature(pii_crypto.decrypt))) == "(ciphertext: str) -> str"
    )
    assert _norm(str(inspect.signature(pii_crypto.hash_field))) == "(plaintext: str) -> str"
