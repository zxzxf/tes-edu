"""T-CAP1 标注审核 API（标注→审核→入库闭环的后半段）。

LLM 预标建议由运维脚本 ``scripts/auto_annotate.py`` 写入 ``status=pending`` 待校队列，
本 API 供教研/管理员审核：
    - approve：二次校验后写回 course_kb 对应字段（difficulty/year/paper_type）+ 留痕；
    - reject：只丢弃建议，不碰题库；
    - stats：产能看板（队列分布 + course_kb 标注覆盖缺口）。

权限沿用合规审核口径：admin / region_admin / teacher。
租户隔离：标注建议表无 tenant_id（题库是平台级数据，沿用 pilot 单租户口径），
故与合规不同——这里不做逐租户过滤，但仍要求登录态与角色，杜绝匿名写回题库。
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import AnnotationSuggestionOut
from app.services import ops

router = APIRouter(prefix="/annotation", tags=["tcap1-annotation"])

_ROLES = (Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)


@router.get("/suggestions", response_model=List[AnnotationSuggestionOut])
async def list_suggestions(
    status: str = Query(None, pattern="^(pending|approved|rejected)$"),
    field_name: str = Query(None, pattern="^(difficulty|year|paper_type)$"),
    limit: int = Query(100, ge=1, le=500),
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """待校队列列表，可按状态/字段过滤。"""
    return await ops.list_annotation_suggestions(db, status=status, field_name=field_name, limit=limit)


@router.get("/stats")
async def stats(
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """产能看板：队列分布 + course_kb 覆盖缺口。"""
    return await ops.annotation_stats(db)


@router.put("/suggestions/{item_id}/review")
async def review(
    item_id: int,
    decision: str = Query(..., pattern="^(approved|rejected)$"),
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """审核一条建议：approve 写回题库 / reject 丢弃。

    非法值或题目不存在时返回 400 且状态保持 pending（不写回也不误标），
    以便人工修正建议或核查数据后再审。
    """
    obj = await ops.get_annotation(db, item_id)
    if not obj:
        raise HTTPException(404, "suggestion not found")
    result = await ops.review_annotation(
        db, obj, decision, actor_id=user.get("user_id")
    )
    if not result["ok"]:
        raise HTTPException(400, result["reason"])
    return {"ok": True, "id": item_id, "status": result["status"]}
