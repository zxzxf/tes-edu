"""后台权限基座 API（T-AM0/T-AM2/T-AM3/T-AM4，T-AM8 安全加固）。

挂在 /api/admin 前缀下（由 Go 网关剥离前缀转发）。
- /tenant       租户/学校基础管理（T-AM0；T-AM8 起按 resolve_scope 做区域/租户过滤）
- /menu/my     按授权返回菜单树+按钮码（T-AM2）
- /menu        菜单管理 CRUD + 按钮级权限码（T-AM3；写操作经 require_perm 强制）
- /role        角色管理 + 菜单授权（T-AM4；GET 回显当前授权，PUT 全量替换经 require_perm 强制）

T-AM8 安全口径：
- 越权统一 403（require_role 粗校验 + require_perm 按钮级 + 跨区/跨租户显式校验）；
- region_admin 辖区回查 sys_user.region_id（JWT 无 region claim）；
- 家长↔学生绑定表属 P4，落地前学生级隔离为租户/区域粗粒度（见总表降级口径）。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.permission import require_perm
from app.core.rbac import Role
from app.core.security import get_current_user, require_role
from app.models import rbac_models as m
from app.services import scope as scope_svc

router = APIRouter(tags=["rbac"])


# ---------------- 请求/响应模型 ----------------
class TenantIn(BaseModel):
    tenant_id: str
    name: str
    region_id: Optional[str] = None
    status: int = 1


class TenantOut(BaseModel):
    tenant_id: str
    name: str
    region_id: Optional[str] = None
    status: int

    model_config = {"from_attributes": True}


class MenuIn(BaseModel):
    menu_id: str
    parent_id: Optional[str] = None
    menu_name: str
    menu_type: str  # DIR / MENU / BUTTON
    path: Optional[str] = None
    component: Optional[str] = None
    icon: Optional[str] = None
    sort: int = 0
    permission_code: Optional[str] = None
    visible: bool = True
    status: int = 1


class MenuOut(BaseModel):
    menu_id: str
    parent_id: Optional[str] = None
    menu_name: str
    menu_type: str
    path: Optional[str] = None
    component: Optional[str] = None
    icon: Optional[str] = None
    sort: int = 0
    permission_code: Optional[str] = None
    visible: bool = True

    model_config = {"from_attributes": True}


class RoleMenuIn(BaseModel):
    menu_ids: List[str]


# ---------------- T-AM0 租户/学校管理 ----------------
@router.get("/tenant", response_model=List[TenantOut])
async def list_tenants(
    region_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.SCHOOL)),
):
    """按数据作用域返回学校列表（T-AM8 区域过滤/租户隔离）。

    - admin        全量（可按 region_id 查询参数再过滤）
    - region_admin 仅本辖区（region_id 回查 sys_user，见 scope.resolve_scope）
    - school       仅本校（tenant_id 取网关注入头 / sys_user.tenant_id）
    """
    scope = await scope_svc.resolve_scope(user, db)
    stmt = select(m.SysTenant)
    if user["role"] == Role.REGION_ADMIN.value and scope["region_id"]:
        stmt = stmt.where(m.SysTenant.region_id == scope["region_id"])
    elif user["role"] == Role.SCHOOL.value and scope["tenant_id"]:
        stmt = stmt.where(m.SysTenant.tenant_id == scope["tenant_id"])
    elif region_id:
        stmt = stmt.where(m.SysTenant.region_id == region_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [TenantOut.model_validate(r) for r in rows]


@router.post("/tenant", response_model=TenantOut, status_code=201)
async def create_tenant(
    body: TenantIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN)),
):
    scope = await scope_svc.resolve_scope(user, db)
    if user["role"] == Role.REGION_ADMIN.value and scope["region_id"]:
        # 区域管理员：要么不传区域（默认落本辖区），要么必须等于辖区，否则 403
        if body.region_id and body.region_id != scope["region_id"]:
            raise HTTPException(status_code=403, detail="只能在本区域下建学校")
        body.region_id = scope["region_id"]
    exists = (await db.execute(
        select(m.SysTenant).where(m.SysTenant.tenant_id == body.tenant_id)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="tenant already exists")
    t = m.SysTenant(**body.model_dump())
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return TenantOut.model_validate(t)


@router.put("/tenant/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: str,
    body: TenantIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN)),
):
    t = (await db.execute(
        select(m.SysTenant).where(m.SysTenant.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="tenant not found")
    scope = await scope_svc.resolve_scope(user, db)
    if user["role"] == Role.REGION_ADMIN.value and scope["region_id"]:
        if t.region_id != scope["region_id"]:
            raise HTTPException(status_code=403, detail="无权修改本区域外学校")
        if body.region_id and body.region_id != scope["region_id"]:
            raise HTTPException(status_code=403, detail="只能在本区域下建学校")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    await db.commit()
    await db.refresh(t)
    return TenantOut.model_validate(t)


# ---------------- T-AM2 菜单树（按授权） ----------------
@router.get("/menu/my", response_model=List[MenuOut])
async def my_menus(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """按当前用户授权返回可见菜单（含按钮节点）。admin 全量。"""
    from app.core import permission as perm_mod

    if user["role"] == Role.ADMIN.value:
        rows = (await db.execute(select(m.SysMenu).order_by(m.SysMenu.sort))).scalars().all()
        return [MenuOut.model_validate(r) for r in rows]

    # 经角色聚合可见菜单
    from sqlalchemy import text

    result = await db.execute(
        text(
            """
            SELECT DISTINCT m.menu_id FROM sys_user_role ur
            JOIN sys_role_menu rm ON rm.role_id = ur.role_id
            JOIN sys_menu m ON m.menu_id = rm.menu_id
            WHERE ur.user_id = :uid
            """
        ),
        {"uid": user["user_id"]},
    )
    ids = [r[0] for r in result.all()]
    if not ids:
        return []
    rows = (await db.execute(
        select(m.SysMenu).where(m.SysMenu.menu_id.in_(ids)).order_by(m.SysMenu.sort)
    )).scalars().all()
    return [MenuOut.model_validate(r) for r in rows]


# ---------------- T-AM3 菜单管理 CRUD ----------------
@router.get("/menu", response_model=List[MenuOut])
async def list_menus(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    rows = (await db.execute(select(m.SysMenu).order_by(m.SysMenu.sort))).scalars().all()
    return [MenuOut.model_validate(r) for r in rows]


@router.post("/menu", response_model=MenuOut, status_code=201)
async def create_menu(
    body: MenuIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:menu:add")),
):
    if body.parent_id:
        parent = (await db.execute(
            select(m.SysMenu).where(m.SysMenu.menu_id == body.parent_id)
        )).scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=400, detail="parent menu not found")
    exists = (await db.execute(
        select(m.SysMenu).where(m.SysMenu.menu_id == body.menu_id)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="menu_id already exists")
    menu = m.SysMenu(**body.model_dump())
    db.add(menu)
    await db.commit()
    await db.refresh(menu)
    return MenuOut.model_validate(menu)


@router.put("/menu/{menu_id}", response_model=MenuOut)
async def update_menu(
    menu_id: str,
    body: MenuIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:menu:edit")),
):
    menu = (await db.execute(
        select(m.SysMenu).where(m.SysMenu.menu_id == menu_id)
    )).scalar_one_or_none()
    if not menu:
        raise HTTPException(status_code=404, detail="menu not found")
    if body.parent_id == menu_id:
        raise HTTPException(status_code=400, detail="menu cannot be its own parent")
    for k, v in body.model_dump().items():
        setattr(menu, k, v)
    await db.commit()
    await db.refresh(menu)
    return MenuOut.model_validate(menu)


@router.delete("/menu/{menu_id}", status_code=204)
async def delete_menu(
    menu_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:menu:delete")),
):
    menu = (await db.execute(
        select(m.SysMenu).where(m.SysMenu.menu_id == menu_id)
    )).scalar_one_or_none()
    if not menu:
        raise HTTPException(status_code=404, detail="menu not found")
    children = (await db.execute(
        select(m.SysMenu).where(m.SysMenu.parent_id == menu_id)
    )).scalars().all()
    if children:
        raise HTTPException(status_code=400, detail="menu has children, delete them first")
    await db.delete(menu)
    await db.commit()
    return None


# ---------------- T-AM4 角色管理 + 菜单授权 ----------------
@router.get("/role", response_model=List[dict])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    rows = (await db.execute(select(m.SysRole))).scalars().all()
    return [
        {"role_id": r.role_id, "role_code": r.role_code, "role_name": r.role_name}
        for r in rows
    ]


@router.get("/role/{role_id}/menus")
async def get_role_menus(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    """返回角色已授权菜单 ID 列表（T-AM4 前端授权树回显）。

    PUT /role/{role_id}/menus 是全量替换语义——前端必须先读回当前授权
    再提交勾选结果，否则空手提交会把角色菜单清空。
    """
    role = (await db.execute(
        select(m.SysRole).where(m.SysRole.role_id == role_id)
    )).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="role not found")
    ids = (await db.execute(
        select(m.SysRoleMenu.menu_id).where(m.SysRoleMenu.role_id == role_id)
    )).scalars().all()
    return {"role_id": role_id, "menu_ids": list(ids)}


@router.put("/role/{role_id}/menus", status_code=204)
async def authorize_role(
    role_id: str,
    body: RoleMenuIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:role:auth")),
):
    role = (await db.execute(
        select(m.SysRole).where(m.SysRole.role_id == role_id)
    )).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="role not found")
    # 全量替换授权
    await db.execute(
        m.SysRoleMenu.__table__.delete().where(m.SysRoleMenu.role_id == role_id)
    )
    for mid in body.menu_ids:
        existing = (await db.execute(
            select(m.SysMenu).where(m.SysMenu.menu_id == mid)
        )).scalar_one_or_none()
        if not existing:
            raise HTTPException(status_code=400, detail=f"menu not found: {mid}")
        db.add(m.SysRoleMenu(role_id=role_id, menu_id=mid))
    await db.commit()
    return None
