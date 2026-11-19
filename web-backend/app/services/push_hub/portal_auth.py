"""第三方 portal 账号体系：自助注册 → admin 审批 → 登录 → 角色门禁。

身份域隔离三条硬墙（计划书 v1.4）：
1. 令牌墙：portal 会话令牌独立密钥+受众（security.py），主站 JWT 解不开它，
   它也不会被主站鉴权链误认——require_account / require_admin 各走各的头；
2. 角色墙：本模块的角色白名单只覆盖推送中心域内接口，新增接口默认不开放；
3. 数据墙：所有查询强制 owner_account_id / scope_app_id SQL 过滤（resolve_owned_app）。

pending 门禁：审批通过前，除"注册结果查询/登录"外一切写接口拒绝。
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.push_hub import PushAccount, PushAccountRole, PushApp
from app.services.push_hub import security

ROLES = ("developer", "operator", "viewer")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: 密码下限：bcrypt 72 字节截断，上限也要防 DoS
PASSWORD_MIN, PASSWORD_MAX = 8, 64


async def register(db: AsyncSession, email: str, password: str, org_name: str,
                   phone: Optional[str] = None, contact: Optional[str] = None) -> PushAccount:
    """自助注册。重复邮箱 409；新账号一律 pending，审批前无写权限。"""
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "邮箱格式无效")
    if not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX):
        raise HTTPException(422, f"密码长度需在 {PASSWORD_MIN}–{PASSWORD_MAX} 之间")
    if not org_name or len(org_name) > 128:
        raise HTTPException(422, "机构名称必填且 ≤128 字")

    dup = (await db.execute(select(PushAccount).where(PushAccount.email == email))).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(409, "该邮箱已注册")

    acc = PushAccount(
        email=email,
        password_hash=security.hash_password(password),
        org_name=org_name,
        phone=phone,
        contact=contact,
        status="pending",
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    # 注册即自带 developer 角色（名下应用管理用），审批只放行"能否用"，不改变角色
    db.add(PushAccountRole(account_id=acc.account_id, role="developer"))
    await db.commit()
    return acc


async def login(db: AsyncSession, email: str, password: str) -> str:
    """登录：返回 portal 会话令牌。

    - pending：403（可登录看状态但写接口拒绝，此处直接给状态码区分）
    - suspended：403
    - 密码错/不存在：401（不区分，防账号枚举）
    """
    email = (email or "").strip().lower()
    acc = (await db.execute(select(PushAccount).where(PushAccount.email == email))).scalar_one_or_none()
    if acc is None or not security.verify_password(password, acc.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
    if acc.status == "suspended":
        raise HTTPException(403, "账号已停用，请联系平台管理员")

    roles = await _role_codes(db, acc.account_id)
    token = security.issue_portal_token(acc.account_id, roles, acc.email)
    if acc.status == "pending":
        # 令牌可发（查进度用），但带状态提示；写接口在 require_account 里被拦
        raise HTTPException(428, "账号待审批，审批通过后即可管理应用与 token")
    return token


async def _role_codes(db: AsyncSession, account_id: int) -> list[str]:
    rows = (
        await db.execute(select(PushAccountRole).where(PushAccountRole.account_id == account_id))
    ).scalars().all()
    return sorted({r.role for r in rows})


async def current_account(db: AsyncSession, claims: dict) -> PushAccount:
    """claims → 账号行（回查 DB：status 以库为准，不独信令牌快照）。"""
    acc = (
        await db.execute(select(PushAccount).where(PushAccount.account_id == int(claims["sub"])))
    ).scalar_one_or_none()
    if acc is None:
        raise HTTPException(401, "账号不存在")
    return acc


def _bearer(request: Request) -> Optional[str]:
    raw = request.headers.get("Authorization") or ""
    if raw.startswith("Bearer "):
        return raw[7:].strip()
    return None


async def require_account(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> tuple[PushAccount, list[str]]:
    """portal 会话门禁：解令牌 → 回查账号 → active 才放行（pending 428/suspended 403）。"""
    claims = security.decode_portal_token(_bearer(request) or "")
    if claims is None:
        raise HTTPException(401, "缺少或无效的 portal 会话令牌")
    acc = await current_account(db, claims)
    if acc.status == "pending":
        raise HTTPException(428, "账号待审批")
    if acc.status == "suspended":
        raise HTTPException(403, "账号已停用")
    roles = await _role_codes(db, acc.account_id)
    if not roles:
        raise HTTPException(403, "账号未授予任何角色")
    return acc, roles


def require_roles(*allowed: str):
    """角色白名单依赖：portal 接口的权限闭包按此声明，新接口不挂即不对第三方开放。"""

    async def _dep(acc_roles: tuple = Depends(require_account)):
        acc, roles = acc_roles
        if not set(allowed) & set(roles):
            raise HTTPException(403, "角色权限不足")
        return acc, roles

    return _dep


async def resolve_owned_app(
    db: AsyncSession, acc: PushAccount, roles: list[str], app_id: int
) -> PushApp:
    """数据墙：app 必须归本账号（且角色 scope 允许），否则 404——不暴露存在性。"""
    app = (await db.execute(select(PushApp).where(PushApp.app_id == app_id))).scalar_one_or_none()
    if app is None or app.owner_account_id != acc.account_id:
        raise HTTPException(404, "应用不存在")
    # scope 限定：只持有 developer(scope=X) 而访问 X 之外的应用 → 同样 404
    scoped = [r for r in (await db.execute(
        select(PushAccountRole).where(PushAccountRole.account_id == acc.account_id)
    )).scalars().all() if r.scope_app_id is not None]
    if scoped and "developer" in roles:
        allowed_ids = {r.scope_app_id for r in scoped if r.role in roles}
        if allowed_ids and app_id not in allowed_ids:
            raise HTTPException(404, "应用不存在")
    return app


async def require_admin(user: dict = Depends(get_current_user)):
    """审批/全局视图走主站 admin 身份（网关注入头），与 portal 令牌完全无关。"""
    if user["role"] != "admin":
        raise HTTPException(403, "仅平台管理员")
    return user
