"""M3-1/M3-2/M3-3/M3-4/M3-5 卷内学习闭环接口。

挂在 ``/api/student``（学生）与 ``/api/admin``（教师）：

学生：
- GET  /student/exams/{assignment_id}/questions/{seq}/solution  解析（提交后才可见）
- GET  /student/exams/{assignment_id}/graph                     按卷知识点子图
- GET  /student/exams/{assignment_id}/questions/{seq}/similar   同知识点同条件变式

教师：
- GET  /admin/exam/papers/{paper_id}/solutions                  整卷解析（教师始终可见）
- POST /admin/exam/question-kp/bind                             批量标注
- POST /admin/exam/question-kp/backfill                         从题库标签回填
- GET  /admin/exam/question-kp/coverage                         覆盖度 + 待标注队列
- GET  /admin/exam/papers/{paper_id}/graph                      按卷图谱
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.exam import (
    _assignment_for_student,
    _load_questions,
    _paper_or_404,
    _tenant_of,
    _to_iso,
)
from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.ops import ExamPaperQuestion, ExamSubmission, QuestionKp
from app.services import graph as graph_svc
from app.services import question_kp as kp_svc
from app.services import similar_kp
from app.services import solution as solution_svc

student_router = APIRouter(prefix="/exams", tags=["student-exam-learning"])
router = APIRouter(prefix="/exam", tags=["exam-learning"])


class SolutionOut(BaseModel):
    paper_id: int
    question_id: int
    solution_steps: Optional[dict] = None
    cached: bool = False
    ok: bool = True
    reason: str = ""
    prompt_version: Optional[int] = None
    model: Optional[str] = None
    generated_at: Optional[str] = None


def _to_solution_out(data: dict) -> SolutionOut:
    return SolutionOut(
        paper_id=data["paper_id"],
        question_id=data["question_id"],
        solution_steps=data.get("solution_steps"),
        cached=bool(data.get("cached")),
        ok=bool(data.get("ok", True)),
        reason=data.get("reason") or "",
        prompt_version=data.get("prompt_version"),
        model=data.get("model"),
        generated_at=data.get("generated_at"),
    )


async def _question_by_seq(
    db: AsyncSession, paper_id: int, seq: int
) -> ExamPaperQuestion:
    row = (
        await db.execute(
            select(ExamPaperQuestion).where(
                ExamPaperQuestion.paper_id == paper_id,
                ExamPaperQuestion.seq == seq,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="question not found")
    return row


async def _submitted(db: AsyncSession, assignment_id: int, student_id: str) -> bool:
    row = (
        await db.execute(
            select(ExamSubmission.id).where(
                ExamSubmission.assignment_id == assignment_id,
                ExamSubmission.student_id == student_id,
            )
        )
    ).first()
    return row is not None


# ============================================================== 学生端
@student_router.get("/{assignment_id}/questions/{seq}/solution", response_model=SolutionOut)
async def student_solution(
    assignment_id: int,
    seq: int,
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """M3-2：**提交前不可查看解析**（防抄），提交后按需生成并缓存。

    返回 403 而不是 404：学生确实能看到这道题，只是现在还不能看解析；
    用 404 会让人以为题不存在。
    """
    a = await _assignment_for_student(db, assignment_id, user)
    if not await _submitted(db, assignment_id, user["user_id"]):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "SOLUTION_LOCKED",
                "message": "交卷后才能查看解析（提交前看解析等于抄答案）",
            },
        )
    q = await _question_by_seq(db, a.paper_id, seq)
    data = await solution_svc.get_or_generate(
        db, tenant_id=_tenant_of(user), paper_id=a.paper_id, question=q,
    )
    await db.commit()
    return _to_solution_out(data)


@student_router.get("/{assignment_id}/graph")
async def student_paper_graph(
    assignment_id: int,
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """M3-4：按卷展示本题涉及知识点及其前驱后继（复用 T-B5 图谱装配）。"""
    a = await _assignment_for_student(db, assignment_id, user)
    return await graph_svc.paper_graph_db(db, a.paper_id, user["user_id"])


@student_router.get("/{assignment_id}/questions/{seq}/similar")
async def student_similar(
    assignment_id: int,
    seq: int,
    n: int = Query(3, ge=1, le=10),
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """M3-5：同知识点**同条件**变式；题库题比本题多限定条件的一律剔除。

    多一个「各项均为正数」就会让标答从 ±2 变成 2——这类候选拿来推题，
    学生按本题条件算出的正确答案会被判错。
    """
    a = await _assignment_for_student(db, assignment_id, user)
    q = await _question_by_seq(db, a.paper_id, seq)
    return await similar_kp.similar_same_conditions(
        db,
        tenant_id=_tenant_of(user),
        paper_question=q,
        n=n,
    )


# ============================================================== 教师端
@router.get("/papers/{paper_id}/solutions", response_model=List[SolutionOut])
async def paper_solutions(
    paper_id: int,
    generate: bool = Query(False, description="缺失时是否现场生成（默认只读缓存）"),
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """整卷解析（教师端始终可见，不受提交状态限制）。"""
    p = await _paper_or_404(db, paper_id, user)
    qs = await _load_questions(db, paper_id)
    out = []
    for q in qs:
        data = await solution_svc.get_or_generate(
            db,
            tenant_id=p.tenant_id or _tenant_of(user),
            paper_id=paper_id,
            question=q,
            allow_generate=generate,
        )
        out.append(_to_solution_out(data))
    if generate:
        await db.commit()
    return out


@router.get("/papers/{paper_id}/graph")
async def paper_graph(
    paper_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M3-4 教师端：按卷聚合知识点 + 前驱后继高亮。"""
    p = await _paper_or_404(db, paper_id, user)
    return await graph_svc.paper_graph_db(db, p.id, None)


class BindRequest(BaseModel):
    question_ids: List[int] = Field(default_factory=list)
    kp_codes: List[str] = Field(default_factory=list)
    source: str = "manual"


class BackfillRequest(BaseModel):
    question_ids: Optional[List[int]] = None


@router.post("/question-kp/bind")
async def bind_question_kp(
    body: BindRequest,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M3-3 批量标注：一次把多道题绑到多个知识点（幂等）。"""
    if not body.question_ids or not body.kp_codes:
        raise HTTPException(status_code=400, detail="question_ids 与 kp_codes 都不能为空")
    out = await kp_svc.bind_kp_codes(
        db,
        tenant_id=_tenant_of(user),
        question_ids=body.question_ids,
        kp_codes=body.kp_codes,
        source=body.source,
        reviewed_by=user.get("user_id"),
    )
    await db.commit()
    return out


@router.post("/question-kp/backfill")
async def backfill_question_kp(
    body: BackfillRequest,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M3-3 从题库 ``knowledge_points`` 回填（只写能解析成 code 的）。"""
    out = await kp_svc.bind_from_course_kb(
        db, tenant_id=_tenant_of(user), question_ids=body.question_ids
    )
    await db.commit()
    return out


@router.get("/question-kp/coverage")
async def question_kp_coverage(
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M3-3 覆盖度看板：按 kp 题量 + 未绑定题队列。"""
    return await kp_svc.coverage(db, _tenant_of(user))