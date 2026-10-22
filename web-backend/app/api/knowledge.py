"""模块1：教研内容管理。

教研员/管理员批量导入、维护知识点、例题、解析。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 kb_knowledge_points）。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import KnowledgePoint
from app.services import ops

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=List[KnowledgePoint])
async def list_knowledge(
    subject: Optional[str] = Query(None, description="按学科过滤"),
    grade: Optional[str] = Query(None, description="按年级过滤"),
    user=Depends(require_role(Role.ADMIN, Role.RESEARCHER)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.list_knowledge(db, subject=subject, grade=grade)


@router.post("", response_model=KnowledgePoint)
async def create_knowledge(
    kp: KnowledgePoint,
    user=Depends(require_role(Role.ADMIN, Role.RESEARCHER)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.create_knowledge(db, kp.model_dump(exclude={"id"}))
