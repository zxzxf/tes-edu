"""题库查询接口（教研内容消费侧）。"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import QuestionOut
from app.services import course_kb as svc

router = APIRouter(prefix="/questions", tags=["questions"])


@router.get("", response_model=List[QuestionOut])
async def list_questions(
    limit: int = Query(50, le=200),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await svc.list_questions(db, limit=limit)


@router.get("/search", response_model=List[QuestionOut])
async def search(
    q: Optional[str] = Query(None, description="关键词（题干/解析模糊匹配）"),
    subject: Optional[str] = None,
    type: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    limit: int = Query(50, le=200),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await svc.search_questions(
        db, q=q, subject=subject, qtype=type, knowledge_point=knowledge_point, limit=limit
    )
