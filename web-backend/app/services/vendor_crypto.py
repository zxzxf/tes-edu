"""MCP 厂商凭证加密（T-AM6）：AES-256-GCM，落库前加密、响应永不回显。

设计口径（与《后台权限与系统集成任务计划书》§五 一致）：
- `sys_service_vendor.credential_enc` / `mcp_service_instance.connection_enc`
  均为 LargeBinary，明文凭证绝不进库、也绝不随 API 响应出库；
- 密钥来源：环境变量 `VENDOR_CREDENTIAL_KEY`（32 字节，hex 或 base64）；
  未配置时从 `JWT_PRIVATE_KEY` 派生（sha256）并告警——仅限开发/联调，
  生产必须显式配置（137 部署时写入 web-backend 环境）。
- 密文格式：nonce(12) ‖ ciphertext ‖ tag(16)，GCM 自带完整性校验，
  篡改会在解密时抛 InvalidTag（由调用方转为 500，不落明文日志）。

纯函数部分（parse_key / derive_key / encrypt_bytes / decrypt_bytes）不依赖
环境与 IO，便于单测；模块级 get_key()/encrypt()/decrypt() 供 API 层调用。
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
            "VENDOR_CREDENTIAL_KEY 未配置，厂商/实例凭证加密使用 JWT_PRIVATE_KEY 派生密钥"
            "（仅限开发联调；生产必须显式配置 32 字节密钥，轮换 JWT 会使旧凭证不可解密）"
        )
        return derive_key(jwt_pem)
    raise RuntimeError(
        "VENDOR_CREDENTIAL_KEY 未配置且无 JWT_PRIVATE_KEY 可派生，拒绝以固定默认密钥加密凭证"
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
    """模块级封装：API 层入库调用（key 缺省走 get_key）。"""
    return encrypt_bytes(key or get_key(), plaintext)


def decrypt(blob: bytes, key: bytes | None = None) -> str:
    """模块级封装：连通性测试等取用调用（key 缺省走 get_key）。"""
    return decrypt_bytes(key or get_key(), blob)
