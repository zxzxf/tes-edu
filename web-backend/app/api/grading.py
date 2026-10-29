"""模块5：教师端批改 = 人工评定工单（M2-5 / M2-6，2026-10-29）。

作业闭环里「转人工」的题（填空判不了 / 解答题）在**学生提交时就地开一张工单**，
教师在这里认领、逐题判定。判定结果**回写** ``exam_submission_items`` 与
``exam_submissions`` 汇总，学生端 ``GET /api/student/exams/{id}/result`` 立刻可见
——不维护第二份成绩，避免「批改系统一套分、学生端一套分」。

四条红线
--------
1. **判定必须留痕**：谁、何时、原值→新值、理由（``exam_grading_revisions``）。
   成绩改动不可追溯 = 没有判分依据。
2. **汇总按 items 全量重算**，不做增量加减。改判一次算错一次就是事故。
3. **跨租户不可见**：拿别人的 ticket_id 一律 404，不区分「不存在」与「不是你校的」。
4. **「学生这种写法也算对」要能沉淀**：``accept_answer`` 把该写法追加进
   ``answer_values``，后续同一道题直接自动判对，不再反复转人工（M2-4 闭环）。

状态约定：``manual_pending``（待人工）→ 教师判定 → ``manual_reviewed``。
人工结果不能写成 ``auto``，否则事后无法区分「机器判的」与「老师判的」。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.equiv import expand_answer_values, split_blanks
from app.core.permission import require_perm
from app.core.rbac import Role
from app.core.security import require_role
from app.models.ops import (
    ExamGradingRevision,
    ExamGradingTicket,
    ExamPaper,
    ExamPaperQuestion,
    ExamSubmission,
    ExamSubmissionItem,
)
from app.models.schemas import (
    GradingClaimRequest,
    GradingJudgeRequest,
    GradingRevisionOut,
    GradingTicketDetail,
    GradingTicketOut,
    GradingTicketPage,
    Submission,
)
from app.services import ops

router = APIRouter(prefix="/grading", tags=["grading"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _tenant(user: dict) -> str:
    return user.get("tenant_id") or "school_pilot_01"


# ======================================================== 旧「模块5」提交批改（保留）
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


# ======================================================== 人工评定工单（M2-5/M2-6）
# 与 exam.py 同一套角色白名单：教师/管理员/学校都能改自己校的卷
TeacherDep = Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL))


# 待认领优先，同状态内新的在前 —— 教师打开页面先看到「没人动过」的
_TODO_ORDER = case(
    (ExamGradingTicket.status == "pending", 0),
    (ExamGradingTicket.status == "claimed", 1),
    else_=2,
)


def _ticket_out(t: ExamGradingTicket, paper_title: str = "") -> GradingTicketOut:
    return GradingTicketOut(
        ticket_id=t.id,
        status=t.status,
        qtype=t.qtype,
        seq=t.seq,
        student_id=t.student_id,
        assignment_id=t.assignment_id,
        paper_id=t.paper_id,
        paper_title=paper_title,
        student_answer=t.student_answer,
        reason=t.reason,
        verdict=t.verdict,
        comment=t.comment,
        assignee=t.assignee,
        reviewed_by=t.reviewed_by,
        reviewed_at=_iso(t.reviewed_at),
        created_at=_iso(t.created_at),
    )


def _revision_out(r: ExamGradingRevision) -> GradingRevisionOut:
    return GradingRevisionOut(
        action=r.action,
        actor_id=r.actor_id,
        actor_role=r.actor_role,
        before_correct=r.before_correct,
        after_correct=r.after_correct,
        before_partial=r.before_partial,
        after_partial=r.after_partial,
        before_status=r.before_status,
        after_status=r.after_status,
        comment=r.comment,
        created_at=_iso(r.created_at),
    )


def _option_list(options) -> list:
    if isinstance(options, dict):
        return [{"label": str(k), "content": str(v)} for k, v in options.items()]
    if isinstance(options, list):
        out = []
        for i, item in enumerate(options):
            if isinstance(item, dict):
                lab = str(item.get("label") or item.get("key") or "")
                content = str(item.get("text") or item.get("value") or "")
            else:
                lab, content = chr(ord("A") + i), str(item)
            if lab:
                out.append({"label": lab, "content": content})
        return out
    return []


async def _ticket_or_404(
    db: AsyncSession, ticket_id: int, user: dict
) -> ExamGradingTicket:
    """按 (id, 本租户) 取工单；跨租户统一 404（红线 3）。"""
    t = (
        await db.execute(
            select(ExamGradingTicket).where(ExamGradingTicket.id == ticket_id)
        )
    ).scalar_one_or_none()
    if not t or (t.tenant_id or "") != _tenant(user):
        raise HTTPException(status_code=404, detail="ticket not found")
    return t


async def _titles(db: AsyncSession, paper_ids: list) -> dict:
    ids = {p for p in paper_ids if p}
    if not ids:
        return {}
    rows = (
        await db.execute(select(ExamPaper.id, ExamPaper.title).where(ExamPaper.id.in_(ids)))
    ).all()
    return {pid: title for pid, title in rows}


async def _ticket_detail(db: AsyncSession, ticket_id: int) -> GradingTicketDetail:
    t = (
        await db.execute(
            select(ExamGradingTicket).where(ExamGradingTicket.id == ticket_id)
        )
    ).scalar_one()
    q = (
        await db.execute(
            select(ExamPaperQuestion).where(ExamPaperQuestion.id == t.paper_question_id)
        )
    ).scalar_one_or_none()
    titles = await _titles(db, [t.paper_id])
    revs = (
        await db.execute(
            select(ExamGradingRevision)
            .where(ExamGradingRevision.ticket_id == t.id)
            .order_by(ExamGradingRevision.created_at, ExamGradingRevision.id)
        )
    ).scalars().all()

    ans = (q.answer or "") if q else ""
    answer_values = list(q.answer_values or []) if q else []
    if not answer_values and ans:
        answer_values = expand_answer_values(ans)  # 老卷未落库时现算，只读展示
    base = _ticket_out(t, titles.get(t.paper_id, ""))
    return GradingTicketDetail(
        **base.model_dump(),
        stem=(q.stem if q else ""),
        options=_option_list(q.options if q else None),
        correct_answer=ans or None,
        analysis=(q.analysis if q else None),
        knowledge_points=(q.knowledge_points or []) if q else [],
        answer_values=answer_values,
        # 多空题的 answer_values 不参与自动判分（见 equiv.blank_judge 红线），如实告知
        answer_values_effective=len(split_blanks(ans)) <= 1,
        revisions=[_revision_out(r) for r in revs],
    )


@router.get("/tickets", response_model=GradingTicketPage)
async def list_tickets(
    status: Optional[str] = Query(None, description="pending/claimed/reviewed；空=全部"),
    paper_id: Optional[int] = None,
    assignment_id: Optional[int] = None,
    student_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user=TeacherDep,
    db: AsyncSession = Depends(get_db),
):
    """待办列表：待认领优先 + 新的在前，附各状态计数供顶部 tab 用。"""
    if status and status not in ("pending", "claimed", "reviewed"):
        raise HTTPException(
            status_code=400, detail="status must be pending/claimed/reviewed"
        )

    base = [ExamGradingTicket.tenant_id == _tenant(user)]
    if paper_id is not None:
        base.append(ExamGradingTicket.paper_id == paper_id)
    if assignment_id is not None:
        base.append(ExamGradingTicket.assignment_id == assignment_id)
    if student_id:
        base.append(ExamGradingTicket.student_id == student_id)

    cond = list(base)
    if status:
        cond.append(ExamGradingTicket.status == status)

    total = (
        await db.execute(select(func.count()).select_from(ExamGradingTicket).where(*cond))
    ).scalar() or 0

    counts = {"pending": 0, "claimed": 0, "reviewed": 0}
    for st, cnt in (
        await db.execute(
            select(ExamGradingTicket.status, func.count())
            .where(*base)
            .group_by(ExamGradingTicket.status)
        )
    ).all():
        counts[st] = cnt

    rows = (
        await db.execute(
            select(ExamGradingTicket)
            .where(*cond)
            .order_by(_TODO_ORDER, ExamGradingTicket.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    titles = await _titles(db, [t.paper_id for t in rows])
    return GradingTicketPage(
        total=total,
        counts=counts,
        page=page,
        page_size=page_size,
        items=[_ticket_out(t, titles.get(t.paper_id, "")) for t in rows],
    )


@router.get("/tickets/{ticket_id}", response_model=GradingTicketDetail)
async def get_ticket(
    ticket_id: int,
    user=TeacherDep,
    db: AsyncSession = Depends(get_db),
):
    """工单详情：题干/选项/标答/解析 + 已确认答案值 + 全部留痕，教师据此判定。"""
    await _ticket_or_404(db, ticket_id, user)  # 先做租户校验
    return await _ticket_detail(db, ticket_id)


@router.post("/tickets/{ticket_id}/claim", response_model=GradingTicketDetail)
async def claim_ticket(
    ticket_id: int,
    body: Optional[GradingClaimRequest] = None,
    user=TeacherDep,
    db: AsyncSession = Depends(get_db),
):
    """认领工单（可选流程）。已被他人认领时**不抢占**，返回 409 让对方先放手。"""
    t = await _ticket_or_404(db, ticket_id, user)
    if t.status == "reviewed":
        raise HTTPException(status_code=409, detail="工单已判定，改判请直接调 judge")
    me = user["user_id"]
    if t.assignee and t.assignee != me:
        raise HTTPException(status_code=409, detail=f"工单已由 {t.assignee} 认领")
    if t.status != "claimed":
        db.add(
            ExamGradingRevision(
                ticket_id=t.id,
                submission_item_id=t.submission_item_id,
                action="claim",
                actor_id=me,
                actor_role=user.get("role"),
                before_status=t.status,
                after_status="claimed",
                comment=(body.comment if body else None),
            )
        )
        t.status = "claimed"
        t.assignee = me
        t.claimed_at = _utcnow()
        t.updated_at = _utcnow()
        await db.commit()
    return await _ticket_detail(db, t.id)


async def _recalc_submission(db: AsyncSession, submission_id: int) -> ExamSubmission:
    """按 items **全量重算**汇总（红线 2：不做增量加减）。"""
    sub = (
        await db.execute(select(ExamSubmission).where(ExamSubmission.id == submission_id))
    ).scalar_one()
    items = (
        await db.execute(
            select(ExamSubmissionItem).where(
                ExamSubmissionItem.submission_id == submission_id
            )
        )
    ).scalars().all()
    correct = sum(1 for it in items if it.correct)
    sub.correct_count = correct
    sub.score = correct
    sub.total_count = len(items)
    sub.status = (
        "manual_pending"
        if any(it.status == "manual_pending" for it in items)
        else "graded"
    )
    return sub


async def _accept_answer(db: AsyncSession, t: ExamGradingTicket, user: dict) -> list:
    """把学生这种写法追加进该题的可接受答案值集合（幂等，去重）。"""
    q = (
        await db.execute(
            select(ExamPaperQuestion).where(ExamPaperQuestion.id == t.paper_question_id)
        )
    ).scalar_one_or_none()
    if not q:
        return []
    vals = list(q.answer_values or [])
    if not vals:
        vals = expand_answer_values(q.answer or "")

    raw = (t.student_answer or "").strip()
    ref_parts = split_blanks(q.answer or "")
    # 多空题按空拆，单空题整体作为一个值
    cands = [
        c for c in (split_blanks(raw) if len(ref_parts) > 1 else [raw]) if c.strip()
    ]

    added = []
    for c in cands:
        if c not in vals:
            vals.append(c)
            added.append(c)
    if not added:
        return []
    q.answer_values = vals
    db.add(
        ExamGradingRevision(
            ticket_id=t.id,
            submission_item_id=t.submission_item_id,
            action="add_value",
            actor_id=user["user_id"],
            actor_role=user.get("role"),
            comment=f"追加可接受答案值：{' / '.join(added)}",
        )
    )
    return added


def _verdict_flags(verdict: str) -> tuple:
    """判定 → ``(correct, partial)``。

    ``partial`` 不计分但保留标记（与选择题「多选漏选但所选皆正确」同一口径）：
    老师给部分分是教学反馈，成绩按题计对错，不做小数计分。
    """
    return verdict == "correct", verdict == "partial"


@router.post("/tickets/{ticket_id}/judge", response_model=GradingTicketDetail)
async def judge_ticket(
    ticket_id: int,
    body: GradingJudgeRequest,
    user=TeacherDep,
    db: AsyncSession = Depends(get_db),
):
    """教师判定（已判定过的再次提交 = 改判，同样留痕）。

    ``accept_answer=true``：确认「学生这种写法也算对」—— 追加进该题
    ``answer_values``，后续同题自动判对，不再反复转人工（M2-4 闭环）。
    """
    t = await _ticket_or_404(db, ticket_id, user)
    item = (
        await db.execute(
            select(ExamSubmissionItem).where(
                ExamSubmissionItem.id == t.submission_item_id
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="submission item not found")

    before = (item.correct, item.partial, item.status)
    correct, partial = _verdict_flags(body.verdict)
    item.correct = correct
    item.partial = partial
    # 人工判定的题不再是 manual_pending；用独立状态与机器判分（auto）区分
    item.status = "manual_reviewed"

    already = t.status == "reviewed"
    db.add(
        ExamGradingRevision(
            ticket_id=t.id,
            submission_item_id=t.submission_item_id,
            action="rejudge" if already else "judge",
            actor_id=user["user_id"],
            actor_role=user.get("role"),
            before_correct=before[0],
            after_correct=correct,
            before_partial=before[1],
            after_partial=partial,
            before_status=before[2],
            after_status=item.status,
            comment=body.comment,
        )
    )

    t.status = "reviewed"
    t.verdict = body.verdict
    t.comment = body.comment
    t.reviewed_by = user["user_id"]
    t.reviewed_at = _utcnow()
    t.updated_at = _utcnow()
    if not t.assignee:
        t.assignee = user["user_id"]

    if body.accept_answer:
        await _accept_answer(db, t, user)

    await _recalc_submission(db, t.submission_id)
    await db.commit()
    return await _ticket_detail(db, t.id)
