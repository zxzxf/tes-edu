"""模块9：合规与内容安全审核。

教研/管理员对入库内容做合规初审；承接缺失项 #8（合规 P0 横切）。
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import ComplianceItem

router = APIRouter(prefix="/compliance", tags=["compliance"])

# 占位内存存储；预置示例审核项（pending 会显示在待审列表）
_STORE: List[ComplianceItem] = [
    ComplianceItem(id=1, type="题目", content="【二次函数】求 y=x²-2x-3 与 x 轴交点", status="pending"),
    ComplianceItem(id=2, type="解析", content="勾股定理应用：梯子靠墙问题标准解答", status="pending"),
    ComplianceItem(id=3, type="图谱", content="知识图谱边：二次函数-相似三角形（易混簇）", status="pending"),
    ComplianceItem(id=4, type="题目", content="被动语态专项练习含答案", status="reviewed"),
    ComplianceItem(id=5, type="解析", content="欧姆定律实验步骤说明", status="rejected"),
]


@router.get("", response_model=List[ComplianceItem])
def list_items(
    status: str = Query(None),
    user=Depends(require_role(Role.ADMIN, Role.RESEARCHER)),
):
    items = _STORE
    if status:
        items = [i for i in items if i.status == status]
    return items


@router.post("", response_model=ComplianceItem)
def create_item(item: ComplianceItem, user=Depends(require_role(Role.ADMIN, Role.RESEARCHER))):
    item.id = len(_STORE) + 1
    _STORE.append(item)
    return item


@router.put("/{item_id}/review")
def review(
    item_id: int,
    status: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.RESEARCHER)),
):
    item = next((i for i in _STORE if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "item not found")
    item.status = status
    return {"ok": True}
