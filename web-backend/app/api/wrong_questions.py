"""模块3：错题本管理。

教师/家长查看学生错题；录入错题（可由 Agent 后端自动同步）。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 wrong_questions）。
"""
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import WrongQuestion
from app.services import ops

router = APIRouter(prefix="/wrong-questions", tags=["wrong-questions"])


@router.get("", response_model=List[WrongQuestion])
async def list_wrong(
    student_id: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.list_wrong(db, student_id)


@router.post("", response_model=WrongQuestion)
async def add_wrong(
    w: WrongQuestion,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.create_wrong(db, w.model_dump(exclude={"id"}))
