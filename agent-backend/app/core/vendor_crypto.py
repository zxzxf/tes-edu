"""BYOM 凭证解密（W6 T-M2，agent-backend 端）。

**必须与 web-backend/app/services/vendor_crypto.py 字节级兼容**——同一张
``tenant_model_config`` 表由 web-backend 加密写入（AES-256-GCM，
``nonce(12) ‖ ciphertext ‖ tag(16)``），agent-backend 在运行态解析时负责解密取用。
两处共用同一主密钥 ``VENDOR_CREDENTIAL_KEY``（32 字节，hex 或 base64）。

若 web-backend 的加解密实现有改动，本文件必须同步，否则租户密钥将无法解密。

纯函数部分（parse_key / derive_key / encrypt_bytes / decrypt_bytes）不依赖环境与 IO，
便于单测；模块级 get_key()/encrypt()/decrypt() 供解析层调用。
"""

import base64
import binascii
import hashlib
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

NONCE_BYTES = 12  # GCM 标准 nonce 长度
KEY_BYTES = 32  # AES-256


def parse_key(raw: str) -> bytes:
    """hex / base64 字符串 → 32 字节密钥（纯函数，可单测）。

    先试 hex（64 字符），再试 base64（44 字符带 padding）；
    两种都不符合 32 字节即抛 ValueError——绝不静默截断。
    """
    s = raw.strip()
    try:
        key = bytes.fromhex(s)
    except (ValueError, binascii.Error):
        try:
            key = base64.b64decode(s, validate=True)
        except (ValueError, binascii.Error) as e:
            raise ValueError("VENDOR_CREDENTIAL_KEY 既不是合法 hex 也不是合法 base64") from e
    if len(key) != KEY_BYTES:
        raise ValueError(f"VENDOR_CREDENTIAL_KEY 必须是 {KEY_BYTES} 字节，当前 {len(key)}")
    return key


def derive_key(secret: str) -> bytes:
    """从任意口令派生 32 字节密钥（sha256；开发兜底路径）。"""
    return hashlib.sha256(secret.encode("utf-8")).digest()


def get_key() -> bytes:
    """进程级密钥解析（环境优先，JWT_PRIVATE_KEY 派生兜底 + 告警）。"""
    raw = os.environ.get("VENDOR_CREDENTIAL_KEY")
    if raw:
        return parse_key(raw)
    jwt_pem = os.environ.get("JWT_PRIVATE_KEY")
    if jwt_pem:
        logger.warning(
            "VENDOR_CREDENTIAL_KEY 未配置，BYOM 凭证解密使用 JWT_PRIVATE_KEY 派生密钥"
            "（仅限开发联调；生产必须显式配置 32 字节密钥，轮换 JWT 会使旧凭证不可解密）"
        )
        return derive_key(jwt_pem)
    raise RuntimeError(
        "VENDOR_CREDENTIAL_KEY 未配置且无 JWT_PRIVATE_KEY 可派生，拒绝以默认密钥处理凭证"
    )


def encrypt_bytes(key: bytes, plaintext: str) -> bytes:
    """AES-256-GCM 加密 → nonce(12) ‖ ct ‖ tag(16)。"""
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def decrypt_bytes(key: bytes, blob: bytes) -> str:
    """解密；blob 过短或 tag 校验失败抛异常（不返回部分明文）。"""
    if not blob or len(blob) <= NONCE_BYTES:
        raise ValueError("credential blob too short")
    nonce, ct = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


def encrypt(plaintext: str, key: bytes | None = None) -> bytes:
    """模块级封装：加密（key 缺省走 get_key）。"""
    return encrypt_bytes(key or get_key(), plaintext)


def decrypt(blob: bytes, key: bytes | None = None) -> str:
    """模块级封装：解密取用（key 缺省走 get_key）。"""
    return decrypt_bytes(key or get_key(), blob)
