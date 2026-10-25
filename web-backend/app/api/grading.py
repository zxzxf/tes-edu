"""模块5：教师端批改与布置。

提交作答 -> 教师评分/写评语。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 submissions）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.permission import require_perm
from app.models.schemas import Submission
from app.services import ops

router = APIRouter(prefix="/grading", tags=["grading"])


@router.post("/submit", response_model=Submission)
async def grade(
    s: Submission,
    # T-AM7：按钮级权限码（替代 require_role 粗粒度白名单）——授权矩阵见
    # rbac_seed.ROLE_MENU_MAP：admin(*:*)/teacher 持 grading:submit。
    user=Depends(require_perm("grading:submit")),
    db: AsyncSession = Depends(get_db),
):
    return await ops.create_submission(db, s.model_dump(exclude={"id"}))


@router.put("/{submission_id}", response_model=Submission)
async def review(
    submission_id: int,
    score: float = Query(...),
    comment: str = "",
    # T-AM7：按钮级权限码（与 submit 同一机制，授权矩阵见 rbac_seed）——
    # admin(*:*)/teacher 持 grading:review。
    user=Depends(require_perm("grading:review")),
    db: AsyncSession = Depends(get_db),
):
    obj = await ops.get_submission(db, submission_id)
    if not obj:
        raise HTTPException(404, "submission not found")
    return await ops.review_submission(db, obj, score, comment)
