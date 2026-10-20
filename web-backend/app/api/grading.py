"""模块5：教师端批改与布置。

提交作答 -> 教师评分/写评语（占位，真实场景对接 Agent 后端判分能力）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import Submission

router = APIRouter(prefix="/grading", tags=["grading"])

_STORE: dict[int, Submission] = {}


@router.post("/submit", response_model=Submission)
def grade(s: Submission, user=Depends(require_role(Role.ADMIN, Role.TEACHER))):
    s.id = len(_STORE) + 1
    _STORE[s.id] = s
    return s


@router.put("/{submission_id}", response_model=Submission)
def review(
    submission_id: int,
    score: float = Query(...),
    comment: str = "",
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
):
    s = _STORE.get(submission_id)
    if not s:
        raise HTTPException(404, "submission not found")
    s.score = score
    s.comment = comment
    return s
