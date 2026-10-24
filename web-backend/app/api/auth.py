"""认证与当前用户信息。

注意：真实环境由认证服务/网关签发 JWT；此处提供占位登录便于端到端联调。
登录成功后前端把 token 带在 Authorization，网关校验后透传身份头到本服务。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.jwt_utils import create_token
from app.core.security import get_current_user
from app.models.schemas import LoginRequest, TokenResponse, UserInfo

router = APIRouter(tags=["auth"])

# 占位用户库（P2 接入 PostgreSQL 后替换为 DB 查询 + 密码哈希校验）
_FAKE_USERS = {
    "researcher@tes.edu": {"password": "demo1234", "role": "researcher", "name": "教研员A"},
    "teacher@tes.edu": {"password": "demo1234", "role": "teacher", "name": "教师B"},
    "parent@tes.edu": {"password": "demo1234", "role": "parent", "name": "家长C"},
    "admin@tes.edu": {"password": "demo1234", "role": "admin", "name": "管理员D"},
    # T-B5：C 端学生占位账号（App 图谱页联调用；role=student 仅可查自己学情）
    "student@tes.edu": {"password": "demo1234", "role": "student", "name": "学生E"},
}


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    u = _FAKE_USERS.get(req.username)
    if not u or u["password"] != req.password:
        raise HTTPException(status_code=401, detail="invalid credentials")
    # 用 python-jose 以 RS256 签发真实 JWT，载荷含 uid/role，私钥对应网关 JWT_PUBLIC_KEY
    token = create_token(user_id=req.username, role=u["role"])
    return TokenResponse(access_token=token, token_type="bearer", role=u["role"])


@router.get("/me", response_model=UserInfo)
def me(user: dict = Depends(get_current_user)):
    return UserInfo(user_id=user["user_id"], role=user["role"])
