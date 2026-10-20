"""JWT 签发工具：供登录接口使用。

与 Go 网关共用同一 JWT_SECRET 与 HS256 算法；网关校验通过后把 uid/role
注入 X-User-Id / X-User-Role 请求头透传给本服务做 RBAC 鉴权。
"""
import os
from datetime import datetime, timedelta, timezone

from jose import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))


def create_token(user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "uid": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
