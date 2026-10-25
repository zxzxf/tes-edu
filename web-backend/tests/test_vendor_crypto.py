"""T-AM6 厂商凭证加密纯函数单测（不连库）。

覆盖：
- parse_key：hex / base64 双格式、长度校验、非法输入抛错（绝不静默截断）
- derive_key：确定性 32 字节
- encrypt_bytes / decrypt_bytes：往返一致、随机 nonce（同明文两次密文不同）、
  篡改检测（GCM tag）、过短 blob 拒绝
"""

import base64
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.services import vendor_crypto as vc

# 同一把 32 字节密钥的 hex 与 base64 两种文本表示（用于双格式解析测试）
KEY_BYTES = bytes([0xAA] * 31 + [0x01])
KEY_HEX = KEY_BYTES.hex()
KEY_B64 = base64.b64encode(KEY_BYTES).decode()


def test_parse_key_hex_and_base64_equivalent():
    k_hex = vc.parse_key(KEY_HEX)
    k_b64 = vc.parse_key(KEY_B64)
    assert len(k_hex) == 32
    assert k_hex == k_b64 == KEY_BYTES


def test_parse_key_rejects_wrong_length_and_garbage():
    with pytest.raises(ValueError):
        vc.parse_key("abcd")  # 太短
    with pytest.raises(ValueError):
        vc.parse_key("!!not-hex-not-base64!!")


def test_derive_key_deterministic_32_bytes():
    k1 = vc.derive_key("same-secret")
    k2 = vc.derive_key("same-secret")
    assert k1 == k2 and len(k1) == 32
    assert vc.derive_key("other") != k1


def test_encrypt_decrypt_roundtrip():
    key = vc.parse_key(KEY_HEX)
    blob = vc.encrypt_bytes(key, "sk-topsecret-123")
    assert isinstance(blob, bytes)
    assert b"sk-topsecret-123" not in blob  # 明文不落密文
    assert vc.decrypt_bytes(key, blob) == "sk-topsecret-123"


def test_encrypt_uses_random_nonce():
    key = vc.parse_key(KEY_HEX)
    b1 = vc.encrypt_bytes(key, "same-plaintext")
    b2 = vc.encrypt_bytes(key, "same-plaintext")
    assert b1 != b2  # 随机 nonce，同明文两次密文不同（防重放/模式分析）
    assert b1[:12] != b2[:12]  # nonce 不同
    assert vc.decrypt_bytes(key, b1) == vc.decrypt_bytes(key, b2)


def test_decrypt_detects_tampering():
    key = vc.parse_key(KEY_HEX)
    blob = bytearray(vc.encrypt_bytes(key, "integrity-check"))
    blob[-1] ^= 0xFF  # 篡改最后一个字节（tag 区）
    with pytest.raises(Exception):
        vc.decrypt_bytes(key, bytes(blob))


def test_decrypt_rejects_short_blob():
    with pytest.raises(ValueError):
        vc.decrypt_bytes(vc.parse_key(KEY_HEX), b"")
    with pytest.raises(ValueError):
        vc.decrypt_bytes(vc.parse_key(KEY_HEX), b"x" * 12)  # 只有 nonce 无 ct/tag


def test_module_level_get_key_env_priority(monkeypatch):
    monkeypatch.setenv("VENDOR_CREDENTIAL_KEY", KEY_HEX)
    assert vc.get_key() == vc.parse_key(KEY_HEX)


def test_module_level_get_key_derives_from_jwt_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("VENDOR_CREDENTIAL_KEY", raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\nfake\n-----END-----")
    with caplog.at_level("WARNING"):
        key = vc.get_key()
    assert key == vc.derive_key(os.environ["JWT_PRIVATE_KEY"])
    assert "VENDOR_CREDENTIAL_KEY" in caplog.text


def test_module_level_get_key_no_source_raises(monkeypatch):
    monkeypatch.delenv("VENDOR_CREDENTIAL_KEY", raising=False)
    monkeypatch.delenv("JWT_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError):
        vc.get_key()
