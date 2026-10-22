"""JWT 签发工具：供登录接口使用。

与 Go 网关配合采用 **RS256 非对称算法**：本服务持有私钥（JWT_PRIVATE_KEY）
签发令牌，网关仅持公钥（JWT_PUBLIC_KEY）验证。私钥泄露无法伪造网关侧
信任的根，且签发/验证职责分离（详见网关侧 internal/auth）。
"""
import os
from datetime import datetime, timedelta, timezone

from jose import jwt

# RS256 私钥（PEM）。务必通过环境变量注入，绝不要硬编码进仓库。
JWT_PRIVATE_KEY = os.getenv("JWT_PRIVATE_KEY", "")
JWT_ALGORITHM = "RS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))


def create_token(user_id: str, role: str) -> str:
    if not JWT_PRIVATE_KEY:
        raise RuntimeError(
            "JWT_PRIVATE_KEY 未配置：RS256 签发需要私钥（PEM），请通过环境变量注入"
        )
    now = datetime.now(timezone.utc)
    payload = {
        "uid": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_PRIVATE_KEY, algorithm=JWT_ALGORITHM)
