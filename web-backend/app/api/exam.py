"""模块4：自动组卷 / 作业闭环 MVP（高三·数学）。

教师端（网关 /api/admin，本文件 ``router`` 挂在 /api/admin/exam）：
- POST   /exam/papers                  规则组卷：从 course_kb 抽题生成试卷（答案随卷快照）
- GET    /exam/papers                  列出本租户试卷
- GET    /exam/papers/{paper_id}       试卷详情（含答案+解析，供教师校对）
- GET    /exam/papers/{paper_id}/validation 发布前校验（dry-run，不落库）
- POST   /exam/papers/{paper_id}/publish   发布（先校验，不通过 409 拒绝）
- POST   /exam/papers/{paper_id}/assign    下发（按年级段 / 学生；须已发布）

学生端（网关 /api/student，本文件 ``student_router`` 挂在 /api/student/exams）：
- GET    /exams                        列出可做的试卷（按年级段下发）
- GET    /exams/{assignment_id}        取卷（题干+选项，不含答案）
- POST   /exams/{assignment_id}/submit 提交并确定性判分（0 token，判据仅本卷答案）
- GET    /exams/{assignment_id}/result 取回判分结果

卷型与题型白名单见 ``app/core/exam_types.py``（M1-3）：纯选择卷含填空/纯填空卷含选择 →
发布拦截；混合卷含解答题 → 不拦，标「需人工 N 道」。

判分设计（ADR-A10）：答案随卷快照在 exam_paper_questions.answer，判分唯一来源就是它，
绝不调 LLM / 不查外部检索。选择题走 ``choice_equivalent``；填空题走 ``blank_judge``
三态（M2-3）：能数值化/区间归一的直接判对错，含变量或符号函数的**转人工而不判错**
（判错不可逆，转人工只是多一道手续）。
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.equiv import (
    blank_judge,
    choice_equivalent,
    expand_answer_values,
    split_blanks,
)
from app.core.exam_types import (
    PAPER_TYPE_LABEL,
    QTYPE_BLANK,
    QTYPE_CHOICE,
    QTYPE_SUBJECTIVE,
    check_choice_answer,
    course_types_for,
    is_known_paper_type,
    to_qtype,
    validate_paper,
)
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import CourseQuestion
from app.models.ops import (
    ExamAssignment,
    ExamGradingTicket,
    ExamPaper,
    ExamPaperQuestion,
    ExamSubmission,
    ExamSubmissionItem,
)
from app.models.schemas import (
    AssignmentCreate,
    AssignmentOut,
    ExamPaperCreate,
    ExamPaperOut,
    ExamPaperQuestionOut,
    ExamResult,
    ExamResultItem,
    PaperValidationOut,
    StudentExamListItem,
    StudentQuestionView,
    SubmitExamRequest,
)

router = APIRouter(prefix="/exam", tags=["exam"])
student_router = APIRouter(prefix="/exams", tags=["student-exam"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- 选项归一
def _options_to_map(options) -> dict:
    """course_kb.options (dict / list) -> {label: content}，供判分识别「学生写数值」写法。"""
    if isinstance(options, dict):
        return {str(k): str(v) for k, v in options.items()}
    if isinstance(options, list):
        out: dict = {}
        for i, item in enumerate(options):
            if isinstance(item, dict):
                lab = str(item.get("label") or item.get("key") or "").strip()
                content = str(item.get("text") or item.get("value") or "")
            else:
                lab = chr(ord("A") + i)
                content = str(item)
            if lab:
                out[lab] = content
        return out
    return {}


def _options_to_list(options) -> list:
    """-> [{label, content}] 供 API 输出。"""
    return [{"label": k, "content": v} for k, v in _options_to_map(options).items()]


def _qtype_from_course(cq_type: str) -> tuple:
    """course_kb.type -> (qtype, multi)；未知题型按选择题兜底（组卷侧已按白名单过滤）。"""
    return to_qtype(cq_type) or (QTYPE_CHOICE, False)


def _answer_acceptable(cq: CourseQuestion) -> bool:
    """抽题时的答案门槛：选择题须 A-D 合规；填空/解答须有非空答案。"""
    ans = (cq.answer or "").strip()
    if not ans:
        return False
    mapped = to_qtype(cq.type)
    qtype, multi = mapped or (QTYPE_CHOICE, False)
    if qtype == QTYPE_CHOICE:
        return check_choice_answer(ans, multi) is None
    return True


# ============================================================== 教师端：组卷
@router.post("/papers", response_model=ExamPaperOut, status_code=201)
async def compose_paper(
    body: ExamPaperCreate,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """规则组卷：从 course_kb 抽题生成试卷（答案随卷快照）。

    过滤：subject / grade / 卷型白名单题型 / tenant / 答案合规 / 有 embedding（已入库质检过）/
    非 quarantine。知识点可选；题源不足显式报错不静默凑卷。
    """
    tenant_id = user.get("tenant_id") or "school_pilot_01"
    if not is_known_paper_type(body.paper_type):
        raise HTTPException(status_code=400, detail=f"未知卷型 {body.paper_type!r}")

    course_types = course_types_for(body.paper_type)
    if not course_types:
        raise HTTPException(status_code=400, detail=f"卷型 {body.paper_type!r} 无对应题型")

    stmt = select(CourseQuestion).where(
        CourseQuestion.subject == body.subject,
        CourseQuestion.grade == body.grade,
        CourseQuestion.type.in_(course_types),
        CourseQuestion.tenant_id == tenant_id,
        CourseQuestion.answer.isnot(None),
        CourseQuestion.embedding.isnot(None),
        (CourseQuestion.source_tag.is_(None)) | (CourseQuestion.source_tag != "quarantine"),
    )
    rows = (await db.execute(stmt)).scalars().all()

    # 答案合规 + 可选知识点过滤（JSON 不支持 SQL 数组算子，放 Python）
    wanted = set(body.knowledge_points)
    cand = []
    for r in rows:
        if not _answer_acceptable(r):
            continue
        if wanted and not wanted.intersection(set(r.knowledge_points or [])):
            continue
        cand.append(r)

    # 随机抽题 + 去重（content_hash）
    random.shuffle(cand)
    seen = set()
    picked = []
    for r in cand:
        if r.content_hash in seen:
            continue
        seen.add(r.content_hash)
        picked.append(r)
        if len(picked) >= body.count:
            break

    if len(picked) < body.count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"题源不足：{body.grade}·{body.subject} 可组"
                f"{PAPER_TYPE_LABEL[body.paper_type]}的题目仅 {len(picked)} 道"
                f"（需 {body.count}）。请降低题量、放宽知识点筛选或改用其他卷型。"
            ),
        )

    title = body.title or (
        f"{body.grade}·{body.subject}·{PAPER_TYPE_LABEL[body.paper_type]}（{len(picked)}题）"
    )
    paper = ExamPaper(
        tenant_id=tenant_id,
        title=title,
        paper_type=body.paper_type,
        grade=body.grade,
        subject=body.subject,
        knowledge_points=body.knowledge_points
        or sorted({kp for r in picked for kp in (r.knowledge_points or [])}),
        created_by=user.get("user_id"),
        published=False,
    )
    db.add(paper)
    await db.flush()  # 取 paper.id

    for seq, r in enumerate(picked, start=1):
        qtype, multi = _qtype_from_course(r.type)
        answer = (r.answer or "").strip()
        db.add(
            ExamPaperQuestion(
                paper_id=paper.id,
                seq=seq,
                content_hash=r.content_hash,
                qtype=qtype,
                multi=multi,
                stem=r.stem,
                options=r.options,
                answer=answer,
                analysis=r.analysis,
                difficulty=r.difficulty,
                knowledge_points=r.knowledge_points,
                # M2-4：标答预展开成可接受答案值集合，判分时命中即判对
                answer_values=expand_answer_values(answer) or None,
            )
        )

    diffs = [r.difficulty for r in picked if r.difficulty]
    paper.difficulty_avg = round(sum(diffs) / len(diffs)) if diffs else None

    await db.commit()
    await db.refresh(paper)
    return await _paper_out(db, paper)


async def _load_questions(db: AsyncSession, paper_id: int) -> list:
    return (
        await db.execute(
            select(ExamPaperQuestion)
            .where(ExamPaperQuestion.paper_id == paper_id)
            .order_by(ExamPaperQuestion.seq)
        )
    ).scalars().all()


async def _paper_out(db: AsyncSession, paper: ExamPaper) -> ExamPaperOut:
    qs = await _load_questions(db, paper.id)
    report = validate_paper(paper.paper_type, qs)
    return ExamPaperOut(
        id=paper.id,
        title=paper.title,
        paper_type=paper.paper_type,
        grade=paper.grade,
        subject=paper.subject,
        published=paper.published,
        question_count=len(qs),
        auto_gradeable_count=report.auto_gradeable_count,
        manual_required_count=report.manual_required_count,
        questions=[
            ExamPaperQuestionOut(
                seq=q.seq,
                qtype=q.qtype,
                multi=q.multi,
                stem=q.stem,
                options=_options_to_list(q.options),
                answer=q.answer,
                analysis=q.analysis,
                difficulty=q.difficulty,
                knowledge_points=q.knowledge_points or [],
                answer_values=_values_of(q),
            )
            for q in qs
        ],
    )


def _values_of(q: ExamPaperQuestion) -> list:
    """该题可接受答案值集合（M2-4）。老卷未落库时现算，不写库。"""
    if q.answer_values:
        return list(q.answer_values)
    return expand_answer_values(q.answer or "")


@router.get("/papers", response_model=list[ExamPaperOut])
async def list_papers(
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = user.get("tenant_id") or "school_pilot_01"
    rows = (
        await db.execute(
            select(ExamPaper)
            .where(ExamPaper.tenant_id == tenant_id)
            .order_by(ExamPaper.created_at.desc())
        )
    ).scalars().all()
    return [await _paper_out(db, p) for p in rows]


@router.get("/papers/{paper_id}", response_model=ExamPaperOut)
async def get_paper(
    paper_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="paper not found")
    return await _paper_out(db, p)


@router.get("/papers/{paper_id}/validation", response_model=PaperValidationOut)
async def validate_paper_for_publish(
    paper_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """发布前校验（dry-run，不落库）。前端在发布前调它展示拦截原因与人工量。"""
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="paper not found")
    report = validate_paper(p.paper_type, await _load_questions(db, paper_id))
    return PaperValidationOut(paper_id=p.id, **report.as_dict())


@router.post("/papers/{paper_id}/publish", response_model=ExamPaperOut)
async def publish_paper(
    paper_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """发布。先跑卷型/题型/答案校验，不通过一律 409 拒绝（不发布半成品）。"""
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="paper not found")

    if p.published:  # 幂等：已发布不再重复校验
        return await _paper_out(db, p)

    report = validate_paper(p.paper_type, await _load_questions(db, paper_id))
    if not report.ok:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PAPER_VALIDATION_FAILED",
                "message": report.note,
                "paper_id": p.id,
                "paper_type": p.paper_type,
                "violations": [v.as_dict() for v in report.violations],
            },
        )

    p.published = True
    p.published_at = _utcnow()
    await db.commit()
    return await _paper_out(db, p)


@router.post("/papers/{paper_id}/assign", response_model=AssignmentOut, status_code=201)
async def assign_paper(
    paper_id: int,
    body: AssignmentCreate,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="paper not found")
    if not p.published:
        raise HTTPException(
            status_code=409,
            detail="试卷未发布，下发后学生端不会出现（请先 POST /papers/{id}/publish）",
        )
    if body.target_type not in ("grade", "student"):
        raise HTTPException(status_code=400, detail="target_type must be grade or student")
    if body.target_type == "student" and not body.target_student:
        raise HTTPException(status_code=400, detail="target_student required for student assign")
    due = datetime.fromisoformat(body.due_at) if body.due_at else None
    assign = ExamAssignment(
        paper_id=paper_id,
        tenant_id=p.tenant_id,
        target_type=body.target_type,
        target_grade=body.target_grade,
        target_student=body.target_student,
        assigned_by=user.get("user_id"),
        due_at=due,
    )
    db.add(assign)
    await db.commit()
    return await _assignment_out(db, assign)


async def _assignment_out(db: AsyncSession, a: ExamAssignment) -> AssignmentOut:
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == a.paper_id))
    ).scalar_one_or_none()
    qc = (
        await db.execute(
            select(func.count())
            .select_from(ExamPaperQuestion)
            .where(ExamPaperQuestion.paper_id == a.paper_id)
        )
    ).scalar() or 0
    return AssignmentOut(
        assignment_id=a.id,
        paper_id=a.paper_id,
        title=p.title if p else "",
        paper_type=p.paper_type if p else "",
        grade=p.grade if p else "",
        subject=p.subject if p else "",
        target_type=a.target_type,
        target_grade=a.target_grade,
        target_student=a.target_student,
        question_count=qc,
        due_at=_to_iso(a.due_at),
    )


# ============================================================== 学生端
def _tenant_of(user: dict) -> str:
    return user.get("tenant_id") or "school_pilot_01"


def _visible_to(a: ExamAssignment, user: dict) -> bool:
    """学生能否看到这份下发。

    **租户隔离是第一道门**：年级段下发（target_type=grade）没有学生 id，
    只按 `target_grade` 命中的话，别的学校的高三学生也会看到这份卷——
    实测 demo 租户的教师卷和试点校学生的列表会互相串。
    """
    tenant = _tenant_of(user)
    if (a.tenant_id or "") != tenant:
        return False
    # MVP：年级段下发仅『高三』（M0-3 student_profile 落地后用学生真实年级替换）
    if a.target_type == "student":
        return a.target_student == user["user_id"]
    return a.target_grade == "高三"


async def _assignment_for_student(
    db: AsyncSession, assignment_id: int, user: dict
) -> ExamAssignment:
    """按 (id, 本租户) 取下发；不可见一律 404，不区分"不存在"与"不是你校的"。"""
    a = (
        await db.execute(select(ExamAssignment).where(ExamAssignment.id == assignment_id))
    ).scalar_one_or_none()
    if not a or not _visible_to(a, user):
        raise HTTPException(status_code=404, detail="assignment not found")
    return a


@student_router.get("", response_model=list[StudentExamListItem])
async def list_student_exams(
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """列出该学生可做的试卷（本租户 + 已发布 + 下发到本人/本年级段）。"""
    student_id = user["user_id"]
    tenant_id = _tenant_of(user)
    grade_rows = (
        await db.execute(
            select(ExamAssignment).where(
                ExamAssignment.tenant_id == tenant_id,
                ExamAssignment.target_type == "grade",
                ExamAssignment.target_grade == "高三",
            ).order_by(ExamAssignment.assigned_at.desc())
        )
    ).scalars().all()
    stu_rows = (
        await db.execute(
            select(ExamAssignment).where(
                ExamAssignment.tenant_id == tenant_id,
                ExamAssignment.target_type == "student",
                ExamAssignment.target_student == student_id,
            )
        )
    ).scalars().all()

    out = []
    for a in list(grade_rows) + list(stu_rows):
        if not _visible_to(a, user):
            continue
        p = (
            await db.execute(select(ExamPaper).where(ExamPaper.id == a.paper_id))
        ).scalar_one_or_none()
        if not p or not p.published:
            continue
        qc = (
            await db.execute(
                select(func.count())
                .select_from(ExamPaperQuestion)
                .where(ExamPaperQuestion.paper_id == a.paper_id)
            )
        ).scalar() or 0
        sub = (
            await db.execute(
                select(ExamSubmission).where(
                    ExamSubmission.assignment_id == a.id,
                    ExamSubmission.student_id == student_id,
                )
            )
        ).scalar_one_or_none()
        out.append(
            StudentExamListItem(
                assignment_id=a.id,
                paper_id=a.paper_id,
                title=p.title,
                paper_type=p.paper_type,
                grade=p.grade,
                subject=p.subject,
                question_count=qc,
                due_at=_to_iso(a.due_at),
                submitted=sub is not None,
            )
        )
    return out


@student_router.get("/{assignment_id}", response_model=list[StudentQuestionView])
async def get_exam_for_student(
    assignment_id: int,
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """取卷：只返回题干 + 选项（不含答案/解析）。"""
    a = await _assignment_for_student(db, assignment_id, user)
    qs = (
        await db.execute(
            select(ExamPaperQuestion)
            .where(ExamPaperQuestion.paper_id == a.paper_id)
            .order_by(ExamPaperQuestion.seq)
        )
    ).scalars().all()
    return [
        StudentQuestionView(
            seq=q.seq,
            qtype=q.qtype,
            multi=q.multi,
            stem=q.stem,
            options=_options_to_list(q.options),
            difficulty=q.difficulty,
            knowledge_points=q.knowledge_points or [],
            # 多空题让 App 能渲染逐空输入（只暴露空数，不暴露答案）
            blank_count=len(split_blanks(q.answer or "")) if q.qtype == QTYPE_BLANK else 0,
        )
        for q in qs
    ]


@student_router.post("/{assignment_id}/submit", response_model=ExamResult)
async def submit_exam(
    assignment_id: int,
    body: SubmitExamRequest,
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """提交并确定性判分（0 token）。每学生每卷一次提交，重复提交返回既有结果。"""
    student_id = user["user_id"]
    a = await _assignment_for_student(db, assignment_id, user)

    # 幂等：已提交则直接返回历史结果
    existing = (
        await db.execute(
            select(ExamSubmission).where(
                ExamSubmission.assignment_id == assignment_id,
                ExamSubmission.student_id == student_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return await _result_out(db, existing)

    qs = (
        await db.execute(
            select(ExamPaperQuestion)
            .where(ExamPaperQuestion.paper_id == a.paper_id)
            .order_by(ExamPaperQuestion.seq)
        )
    ).scalars().all()
    ans_by_seq = {it.seq: it for it in body.answers}

    items = []
    correct_count = 0
    manual = False
    for q in qs:
        submitted = ans_by_seq.get(q.seq)
        stu = (submitted.student_answer or "") if submitted else ""
        blanks = submitted.blanks if submitted else None
        it = _grade_one(q, stu, blanks)
        if it["status"] == "manual_pending":
            manual = True
        if it["correct"]:
            correct_count += 1
        items.append(it)

    sub = ExamSubmission(
        assignment_id=assignment_id,
        paper_id=a.paper_id,
        student_id=student_id,
        tenant_id=user.get("tenant_id"),
        client_id=body.client_id,
        status="manual_pending" if manual else "graded",
        score=correct_count,
        correct_count=correct_count,
        total_count=len(qs),
        submitted_at=_utcnow(),
    )
    db.add(sub)
    await db.flush()
    item_objs = []
    for it in items:
        obj = ExamSubmissionItem(
            submission_id=sub.id,
            paper_question_id=it["paper_question_id"],
            seq=it["seq"],
            qtype=it["qtype"],
            student_answer=it["student_answer"],
            correct=it["correct"],
            partial=it["partial"],
            status=it["status"],
        )
        db.add(obj)
        item_objs.append((it, obj))
    await db.flush()  # 取 item.id 才能开单

    # M2-5：转人工的题**提交时就地开一张工单**，教师端待办立刻可见。
    # 不开单的话 teacher 只能靠翻 DB 找出 manual_pending，闭环缺最后一环。
    for it, obj in item_objs:
        if it["status"] != "manual_pending":
            continue
        db.add(
            ExamGradingTicket(
                tenant_id=user.get("tenant_id") or a.tenant_id,
                submission_id=sub.id,
                submission_item_id=obj.id,
                assignment_id=assignment_id,
                paper_id=a.paper_id,
                paper_question_id=it["paper_question_id"],
                student_id=student_id,
                seq=it["seq"],
                qtype=it["qtype"],
                student_answer=(it["student_answer"] or "")[:4000],
                reason=(it["reason"] or "机器判不了，需人工评定")[:255],
                status="pending",
            )
        )

    await db.commit()
    await db.refresh(sub)
    return await _result_out(db, sub)


def _grade_one(
    q: ExamPaperQuestion,
    student_answer: str,
    blanks: Optional[list] = None,
) -> dict:
    """对单题做确定性判分（M1-4 题型→判分路由，M2-3 填空三态）。

    - ``choice``：``choice_equivalent``（0 token，单选/多选、连写答案均支持）
    - ``blank``：``blank_judge`` **三态** —— ``auto`` 直接给对/错；``manual``
      （含变量/符号函数、多空空数不符）转人工，**绝不硬判错**
    - ``subjective``：直接人工工单，不调 LLM

    红线：解析不了的结构一律转人工。成绩判错不可逆，转人工只是多一道手续。
    """
    if q.qtype == QTYPE_CHOICE:
        opt_map = _options_to_map(q.options)
        res = choice_equivalent(student_answer, q.answer, multi=q.multi, options=opt_map)
        if res is True:
            return _item(q, student_answer, correct=True, partial=False, status="auto")
        if res == "partial":  # 多选漏选但所选皆正确（MVP 标记不计分）
            return _item(q, student_answer, correct=False, partial=True, status="auto")
        return _item(q, student_answer, correct=False, partial=False, status="auto")

    if q.qtype == QTYPE_BLANK:
        # 逐空提交优先；未逐空提交时拼回可展示形态（结果页要能看懂学生答了什么）
        parts = [str(x or "").strip() for x in (blanks or [])]
        display = student_answer or ("；".join(parts) if parts else "")
        v = blank_judge(
            display, q.answer or "", blanks=blanks or None, values=_values_of(q)
        )
        if v["status"] == "manual":
            return _item(
                q, display, correct=None, partial=False,
                status="manual_pending", reason=v["reason"],
            )
        return _item(
            q, display, correct=bool(v["correct"]), partial=False,
            status="auto", reason=v["reason"],
        )

    return _item(
        q, student_answer, correct=None, partial=False,
        status="manual_pending", reason="解答题需人工批改",
    )


def _item(q, student_answer, *, correct, partial, status, reason=None) -> dict:
    return {
        "paper_question_id": q.id,
        "seq": q.seq,
        "qtype": q.qtype,
        "student_answer": student_answer,
        "correct": correct,
        "partial": partial,
        "status": status,
        "reason": reason,
    }


def _manual_reason(it, q) -> Optional[str]:
    """转人工原因（结果回看用）。

    ``reason`` 不落库（改表成本高于重算）：这里用同一套纯函数重算一次，
    0 token、无副作用，保证「提交当下」与「事后回看」给出的原因一致。
    """
    if it.status == "manual_reviewed":
        return "老师已复核判定"
    if it.status != "manual_pending":
        return None
    if q is not None and q.qtype == QTYPE_BLANK:
        return blank_judge(it.student_answer or "", q.answer or "")["reason"] or None
    if q is not None and q.qtype == QTYPE_SUBJECTIVE:
        return "解答题需人工批改"
    return None


@student_router.get("/{assignment_id}/result", response_model=ExamResult)
async def get_result(
    assignment_id: int,
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """取回判分结果（已提交才有）。"""
    # 先校验下发对本租户/本人可见：否则拿到别人学校的 assignment_id 就能翻成绩
    await _assignment_for_student(db, assignment_id, user)
    sub = (
        await db.execute(
            select(ExamSubmission).where(
                ExamSubmission.assignment_id == assignment_id,
                ExamSubmission.student_id == user["user_id"],
            )
        )
    ).scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="not submitted yet")
    return await _result_out(db, sub)


async def _result_out(db: AsyncSession, sub: ExamSubmission) -> ExamResult:
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == sub.paper_id))
    ).scalar_one_or_none()
    items = (
        await db.execute(
            select(ExamSubmissionItem)
            .where(ExamSubmissionItem.submission_id == sub.id)
            .order_by(ExamSubmissionItem.seq)
        )
    ).scalars().all()
    q_by_id = {}
    if items:
        pqs = (
            await db.execute(
                select(ExamPaperQuestion).where(
                    ExamPaperQuestion.id.in_([it.paper_question_id for it in items])
                )
            )
        ).scalars().all()
        q_by_id = {q.id: q for q in pqs}
    manual_pending = sum(1 for it in items if it.status == "manual_pending")
    reviewed = sum(1 for it in items if it.status == "manual_reviewed")
    # 老师评语随结果返回：学生要知道「为什么算错」，不然改判等于黑箱
    comments = {}
    if reviewed:
        for tid, sid, cm in (
            await db.execute(
                select(
                    ExamGradingTicket.submission_item_id,
                    ExamGradingTicket.verdict,
                    ExamGradingTicket.comment,
                ).where(ExamGradingTicket.submission_id == sub.id)
            )
        ).all():
            if cm:
                comments[tid] = cm
    return ExamResult(
        submission_id=sub.id,
        assignment_id=sub.assignment_id,
        paper_id=sub.paper_id,
        title=p.title if p else "",
        score=float(sub.score or 0),
        correct_count=sub.correct_count or 0,
        total_count=sub.total_count or 0,
        auto_gradeable_count=max(len(items) - manual_pending, 0),
        manual_pending_count=manual_pending,
        manual_reviewed_count=reviewed,
        status=sub.status,
        submitted_at=_to_iso(sub.submitted_at),
        items=[
            ExamResultItem(
                seq=it.seq,
                qtype=it.qtype,
                multi=(q_by_id[it.paper_question_id].multi if it.paper_question_id in q_by_id else False),
                student_answer=it.student_answer,
                correct_answer=(
                    q_by_id[it.paper_question_id].answer if it.paper_question_id in q_by_id else None
                ),
                correct=it.correct or False,
                partial=it.partial or False,
                status=it.status,
                reason=_manual_reason(it, q_by_id.get(it.paper_question_id)),
                analysis=(
                    q_by_id[it.paper_question_id].analysis if it.paper_question_id in q_by_id else None
                ),
                teacher_comment=comments.get(it.id),
            )
            for it in items
        ],
    )
