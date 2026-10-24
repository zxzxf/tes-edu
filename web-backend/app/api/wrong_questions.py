"""模块3：错题本管理。

教师/家长查看学生错题；录入错题（可由 Agent 后端自动同步）。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 wrong_questions）。
2026-10-24（S5 ④）：列表返回时按 (content_hash, wrong_step_no) 关联 course_kb_steps，
补上该步的正确做法(correction)、步骤详解与知识点卡，让错题本"知道错在哪一步、怎么改"。
"""
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import WrongQuestion, WrongQuestionOut
from app.services import ops

router = APIRouter(prefix="/wrong-questions", tags=["wrong-questions"])


@router.get("", response_model=List[WrongQuestionOut])
async def list_wrong(
    student_id: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    rows = await ops.list_wrong(db, student_id)
    return await ops.enrich_wrong(db, rows)


@router.post("", response_model=WrongQuestion)
async def add_wrong(
    w: WrongQuestion,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.create_wrong(db, w.model_dump(exclude={"id"}))
