"""推送中心安全基元：token 生成/哈希、portal 会话令牌、渠道凭证加解密、密码哈希。

身份域隔离（P1 底线，见 docx/通用App推送中心实施计划.md v1.4）：
- portal 会话令牌 = HS256 + **独立密钥**（PUSH_PORTAL_SECRET）+ 固定受众
  ``aud="push-hub-portal"`` + ``typ="push_portal"``——主站 JWT 由 Go 网关 RSA 验签
  后注入 X-User-* 头，与本令牌算法/密钥/载体（Authorization vs 注入头）三重不同，
  互不互认是构造性成立的，不是靠约定。
- 推送 token（pth-）走独立 Bearer 校验路径（tokens.py），同样不进 JWT 体系。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from app.core.config import settings

#: portal 会话受众/类型标记：校验双确认，任何一项不符即 401
PUSH_AUD = "push-hub-portal"
PUSH_TYP = "push_portal"
PORTAL_TOKEN_TTL_MIN = 120  # 会话 2 小时，过期重新登录


def hash_push_token(raw: str) -> str:
    """推送 token 哈希：SHA-256 hex（库内只存哈希，明文仅签发时回显一次）。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_push_token() -> tuple[str, str, str]:
    """签发推送 token。

    返回 (明文, 哈希, 展示前缀)。明文形如 pth-<32hex>；
    secrets.token_hex 走 CSPRNG，与 auth 路由口令生成同源。
    """
    body = secrets.token_hex(16)  # 32 hex
    raw = f"pth-{body}"
    prefix = f"pth-{body[:4]}"
    return raw, hash_push_token(raw), prefix


def issue_portal_token(account_id: int, roles: list[str], email: str) -> str:
    """portal 会话令牌：独立密钥+独立受众，主站体系不可解、不可认。"""
    if not settings.push_portal_secret:
        raise RuntimeError("PUSH_PORTAL_SECRET 未配置，portal 登录拒绝（fail-closed）")
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(account_id),
        "typ": PUSH_TYP,
        "aud": PUSH_AUD,
        "email": email,
        "roles": roles,  # 角色快照；撤销/降权在 require 依赖里回查 DB，不独信 claim
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=PORTAL_TOKEN_TTL_MIN)).timestamp()),
    }
    return jwt.encode(claims, settings.push_portal_secret, algorithm="HS256")


def decode_portal_token(token: str) -> Optional[dict]:
    """解 portal 会话令牌；任何异常一律 None（401 由调用方抛），绝不抛给路由层。"""
    if not token or not settings.push_portal_secret:
        return None
    try:
        claims = jwt.decode(
            token,
            settings.push_portal_secret,
            algorithms=["HS256"],
            audience=PUSH_AUD,
        )
    except JWTError:
        return None
    # 双确认受众类型：主站令牌（无 typ 或 typ 不同）绝不可能被当 portal 用
    if claims.get("typ") != PUSH_TYP or claims.get("aud") != PUSH_AUD:
        return None
    return claims


# ---------------------------------------------------------------- 密码哈希
# 与 rbac_seed 同版本约束：passlib 1.7.4 + bcrypt 4.1.3（见 requirements 注释）。
_ctx = None


def get_pwd_ctx():
    global _ctx
    if _ctx is None:
        from passlib.context import CryptContext

        _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return _ctx


def hash_password(plain: str) -> str:
    return get_pwd_ctx().hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return get_pwd_ctx().verify(plain, hashed)
    except ValueError:
        return False


# ---------------------------------------------------------------- 渠道凭证加密
def enc_channel_config(plain_json: str) -> str:
    """渠道凭证 Fernet 加密（密文串落 push_channel.config['enc']）。"""
    from cryptography.fernet import Fernet

    if not settings.push_secret_key:
        raise RuntimeError("PUSH_SECRET_KEY 未配置，渠道凭证无法加密存储（fail-closed）")
    return Fernet(settings.push_secret_key.encode("utf-8")).encrypt(
        plain_json.encode("utf-8")
    ).decode("utf-8")


def dec_channel_config(enc_str: str) -> str:
    """解密渠道凭证；密钥不符/密文损坏抛 InvalidToken，由调用方兜。"""
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401  调用方捕获

    if not settings.push_secret_key:
        raise RuntimeError("PUSH_SECRET_KEY 未配置，渠道凭证不可解")
    return Fernet(settings.push_secret_key.encode("utf-8")).decrypt(
        enc_str.encode("utf-8")
    ).decode("utf-8")
