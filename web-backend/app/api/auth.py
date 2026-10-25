"""认证与当前用户信息（T-AM1 升级）。

- 真实环境由 web-backend 作为后台用户权威库（D7）：`sys_user` 存密码哈希，
  `/login` 校验密码后由 web-backend 签发 JWT，沿用网关同一 RSA 密钥对。
- DB 未就绪时回退到 `_FAKE_USERS`（过渡兼容，保证离线单测仍可跑）。
- C 端 student 账号由网关管登录，此处仅 B 端后台用户；student@tes.edu 占位保留。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.jwt_utils import create_token
from app.core.security import get_current_user
from app.models.schemas import LoginRequest, TokenResponse, UserInfo

router = APIRouter(tags=["auth"])

# 过渡兼容占位库（DB 就绪后由 sys_user 查询替代）；student 仅 App 端占位。
_DEMO_TENANT = "demo"
_FAKE_USERS = {
    "researcher@tes.edu": {"password": "demo1234", "role": "researcher", "name": "教研员A", "tenant_id": _DEMO_TENANT},
    "teacher@tes.edu": {"password": "demo1234", "role": "teacher", "name": "教师B", "tenant_id": _DEMO_TENANT},
    "parent@tes.edu": {"password": "demo1234", "role": "parent", "name": "家长C", "tenant_id": _DEMO_TENANT},
    "admin@tes.edu": {"password": "demo1234", "role": "admin", "name": "管理员D", "tenant_id": _DEMO_TENANT},
    "student@tes.edu": {"password": "demo1234", "role": "student", "name": "学生E"},
}


async def _lookup_user(username: str) -> dict | None:
    """从 sys_user 查后台用户；DB 不可用返回 None（调用方回退占位库）。"""
    try:
        from sqlalchemy import select

        from app.core.db import get_db
        from app.models import rbac_models as m

        async for db in get_db():
            row = (await db.execute(
                select(m.SysUser).where(m.SysUser.username == username)
            )).scalar_one_or_none()
            if not row:
                return None
            # 聚合角色（取第一个有效角色；多角色场景后续扩展）
            role_row = (await db.execute(
                select(m.SysRole.role_code)
                .join(m.SysUserRole, m.SysUserRole.role_id == m.SysRole.role_id)
                .where(m.SysUserRole.user_id == row.user_id)
                .limit(1)
            )).scalar_one_or_none()
            return {
                "user_id": row.user_id,
                "password_hash": row.password_hash,
                "role": role_row,
                "tenant_id": row.tenant_id,
                "region_id": row.region_id,
                "is_db": True,
            }
    except Exception:  # noqa: BLE001  DB 未就绪/未建表 → 回退占位
        return None


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    db_user = await _lookup_user(req.username)
    if db_user is None:
        # 过渡兼容占位库
        u = _FAKE_USERS.get(req.username)
        if not u or u["password"] != req.password:
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = create_token(user_id=req.username, role=u["role"], tenant_id=u.get("tenant_id"))
        return TokenResponse(access_token=token, token_type="bearer", role=u["role"])

    # DB 用户：校验 bcrypt 哈希
    from passlib.context import CryptContext

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    if not db_user.get("password_hash") or not pwd.verify(req.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = create_token(
        user_id=db_user["user_id"],
        role=db_user["role"],
        tenant_id=db_user.get("tenant_id"),
    )
    return TokenResponse(access_token=token, token_type="bearer", role=db_user["role"])


@router.get("/me", response_model=UserInfo)
def me(user: dict = Depends(get_current_user)):
    return UserInfo(user_id=user["user_id"], role=user["role"])
