"""数据字典 API（T-DICT，2026-11-14）。

挂在 /api/admin 前缀下（由 Go 网关剥离前缀转发）。
树形结构：分类为根节点（parent_id 空），条目为子节点（继承父节点 dict_type）。

- GET  /dict          列出全部字典节点（扁平，前端建树）；任何已登录角色可读
- GET  /dict/map?type= 返回某分类下 {dict_code: dict_name}，供各页面做"编码→名称"翻译；
                      任何已登录角色可读（用户管理列表 school 也要用，故放开）
  说明：字典是只读配置数据，且「数据字典」菜单已对各角色授权可见，
  故读接口与菜单可见性保持一致——看得见菜单就应看得见数据（写仍仅 admin）。
- POST /dict          新建节点（admin）
- PUT  /dict/{id}     更新节点（admin）
- DELETE /dict/{id}   删除节点（admin；有子节点时拒绝 400）
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import get_current_user, require_role
from app.models import rbac_models as m

router = APIRouter(tags=["dict"])


# ---------------- 请求/响应模型 ----------------
class DictIn(BaseModel):
    dict_id: str
    parent_id: Optional[str] = None
    dict_type: str  # 分类码（根节点即分类本身）
    dict_name: str
    dict_code: str
    dict_value: Optional[str] = None
    sort: int = 0
    status: int = 1
    remark: Optional[str] = None


class DictOut(BaseModel):
    dict_id: str
    parent_id: Optional[str] = None
    dict_type: str
    dict_name: str
    dict_code: str
    dict_value: Optional[str] = None
    sort: int
    status: int
    remark: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------- 读取：列表（任意已登录角色） / 增删改（admin） ----------------
@router.get("/dict", response_model=List[DictOut])
async def list_dict(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """列出全部字典节点（含分类与条目），前端按 parent_id 建树渲染。

    字典属只读基础配置，「数据字典」菜单对 school / region_admin 等角色可见，
    故此处与菜单可见性对齐放通读取；细粒度边界由网关 /api/admin/ 角色白名单兜住。
    写操作（POST/PUT/DELETE）仍仅 admin，前端按 isAdmin 隐藏写入口。
    """
    rows = (await db.execute(select(m.SysDict).order_by(m.SysDict.sort))).scalars().all()
    return [DictOut.model_validate(r) for r in rows]


@router.post("/dict", response_model=DictOut, status_code=201)
async def create_dict(
    body: DictIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    if body.parent_id:
        parent = (await db.execute(
            select(m.SysDict).where(m.SysDict.dict_id == body.parent_id)
        )).scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=400, detail="parent dict not found")
    dup = (await db.execute(
        select(m.SysDict).where(
            (m.SysDict.dict_id == body.dict_id)
            | (
                (m.SysDict.dict_type == body.dict_type)
                & (m.SysDict.dict_code == body.dict_code)
            )
        )
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="dict_id 或 (dict_type, dict_code) 已存在")
    node = m.SysDict(
        dict_id=body.dict_id,
        parent_id=body.parent_id or None,
        dict_type=body.dict_type,
        dict_name=body.dict_name,
        dict_code=body.dict_code,
        dict_value=body.dict_value if body.dict_value is not None else body.dict_code,
        sort=body.sort,
        status=body.status if body.status in (0, 1) else 1,
        remark=body.remark,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return DictOut.model_validate(node)


@router.put("/dict/{dict_id}", response_model=DictOut)
async def update_dict(
    dict_id: str,
    body: DictIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    node = (await db.execute(
        select(m.SysDict).where(m.SysDict.dict_id == dict_id)
    )).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="dict not found")
    if body.parent_id == dict_id:
        raise HTTPException(status_code=400, detail="dict cannot be its own parent")
    # 改 parent 时校验新父存在
    if body.parent_id:
        parent = (await db.execute(
            select(m.SysDict).where(m.SysDict.dict_id == body.parent_id)
        )).scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=400, detail="parent dict not found")
    # 改 (type, code) 时校验不与其它节点冲突
    if (body.dict_type, body.dict_code) != (node.dict_type, node.dict_code):
        conflict = (await db.execute(
            select(m.SysDict).where(
                (m.SysDict.dict_type == body.dict_type)
                & (m.SysDict.dict_code == body.dict_code)
                & (m.SysDict.dict_id != dict_id)
            )
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=409, detail="(dict_type, dict_code) 已存在")
    node.parent_id = body.parent_id or None
    node.dict_type = body.dict_type
    node.dict_name = body.dict_name
    node.dict_code = body.dict_code
    node.dict_value = body.dict_value if body.dict_value is not None else body.dict_code
    node.sort = body.sort
    node.status = body.status if body.status in (0, 1) else node.status
    node.remark = body.remark
    await db.commit()
    await db.refresh(node)
    return DictOut.model_validate(node)


@router.delete("/dict/{dict_id}", status_code=204)
async def delete_dict(
    dict_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    node = (await db.execute(
        select(m.SysDict).where(m.SysDict.dict_id == dict_id)
    )).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="dict not found")
    children = (await db.execute(
        select(m.SysDict).where(m.SysDict.parent_id == dict_id)
    )).scalars().all()
    if children:
        raise HTTPException(
            status_code=400,
            detail=f"dict has {len(children)} children, delete them first",
        )
    await db.delete(node)
    await db.commit()
    return None


# ---------------- 翻译映射：任意已登录角色可读 ----------------
@router.get("/dict/map")
async def dict_map(
    type: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """返回某分类下 {dict_code: dict_name}，供各页面把编码翻译成可读名。

    school 也要读（用户管理列表显示角色名），故用 get_current_user 放通，
    细粒度权限由网关角色白名单兜住。仅返回启用的叶子条目（parent_id 非空）。
    """
    rows = (await db.execute(
        select(m.SysDict).where(
            m.SysDict.dict_type == type,
            m.SysDict.parent_id.is_not(None),
            m.SysDict.status == 1,
        )
    )).scalars().all()
    return {r.dict_code: r.dict_name for r in rows}
