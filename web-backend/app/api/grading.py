"""模块5：教师端批改与布置。

提交作答 -> 教师评分/写评语。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 submissions）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import Submission
from app.services import ops

router = APIRouter(prefix="/grading", tags=["grading"])


@router.post("/submit", response_model=Submission)
async def grade(
    s: Submission,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    return await ops.create_submission(db, s.model_dump(exclude={"id"}))


@router.put("/{submission_id}", response_model=Submission)
async def review(
    submission_id: int,
    score: float = Query(...),
    comment: str = "",
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    obj = await ops.get_submission(db, submission_id)
    if not obj:
        raise HTTPException(404, "submission not found")
    return await ops.review_submission(db, obj, score, comment)
