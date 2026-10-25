"""模块9：合规与内容安全审核。

教研/管理员对入库内容做合规初审；承接缺失项 #8（合规 P0 横切）。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 compliance_items）。
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import ComplianceItem
from app.services import ops

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("", response_model=List[ComplianceItem])
async def list_items(
    status: str = Query(None),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.list_compliance(db, status=status)


@router.post("", response_model=ComplianceItem)
async def create_item(
    item: ComplianceItem,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.create_compliance(db, item.model_dump(exclude={"id"}))


@router.put("/{item_id}/review")
async def review(
    item_id: int,
    status: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    obj = await ops.get_compliance(db, item_id)
    if not obj:
        raise HTTPException(404, "item not found")
    await ops.review_compliance(db, obj, status)
    return {"ok": True}
