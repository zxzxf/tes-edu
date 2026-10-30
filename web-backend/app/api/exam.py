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
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.answer_verify import status_label as answer_status_label
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
# 判据来源编码（M2-7）：落库到 exam_submission_items.grading_source，指标面板按它出分布。
# 别名带 M_ 前缀，避免与本模块已有的 SOURCE_* 语义混淆。
from app.core.grading_metrics import (
    SOURCE_CHOICE as M_SOURCE_CHOICE,
    SOURCE_MANUAL_FROZEN as M_SOURCE_FROZEN,
    SOURCE_MANUAL_SUBJECTIVE as M_SOURCE_SUBJECTIVE,
    SOURCE_UNKNOWN as M_SOURCE_UNKNOWN,
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
    AnswerCheckConfirmRequest,
    AnswerCheckListOut,
    AnswerCheckOut,
    AnswerCheckSummaryOut,
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
from app.services import answer_verify as verify
from app.services import grading_guard as guard
from app.services import wrongbook

router = APIRouter(prefix="/exam", tags=["exam"])
student_router = APIRouter(prefix="/exams", tags=["student-exam"])


class QuestionReviewRequest(BaseModel):
    status: Literal["draft", "published", "quarantine"]


def _review_out(q: CourseQuestion) -> dict:
    return {
        "id": q.id, "subject": q.subject, "grade": q.grade, "type": q.type,
        "stem": q.stem, "options": _options_to_list(q.options), "answer": q.answer,
        "analysis": q.analysis, "knowledge_points": q.knowledge_points or [],
        "source_tag": q.source_tag,
        "status": q.status, "reviewed_by": q.reviewed_by,
        "reviewed_at": _to_iso(q.reviewed_at),
    }


@router.get("/questions")
async def list_review_questions(
    status: Optional[Literal["draft", "published", "quarantine"]] = None,
    limit: int = 50,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CourseQuestion).where(CourseQuestion.tenant_id == _tenant_of(user))
    if status:
        stmt = stmt.where(CourseQuestion.status == status)
    rows = (await db.execute(stmt.order_by(CourseQuestion.id.desc()).limit(max(1, min(limit, 200))))).scalars().all()
    return [_review_out(q) for q in rows]


@router.post("/questions/{question_id}/review")
async def review_question(
    question_id: int,
    body: QuestionReviewRequest,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    q = (await db.execute(select(CourseQuestion).where(
        CourseQuestion.id == question_id,
        CourseQuestion.tenant_id == _tenant_of(user),
    ))).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="question not found")
    if body.status == "published" and not _answer_acceptable(q):
        raise HTTPException(status_code=409, detail="题目答案缺失或不合规，不能通过审核")
    q.status = body.status
    if q.status == "published" and q.source_tag == "quarantine":
        q.source_tag = "reviewed"
    q.reviewed_by = user.get("user_id")
    q.reviewed_at = _utcnow()
    await db.commit()
    return _review_out(q)


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
        CourseQuestion.status == "published",
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


async def _unreviewed_violations(db, tenant_id: str, questions) -> list:
    hashes = {q.content_hash for q in questions if q.content_hash}
    if not hashes:
        return []
    rows = (await db.execute(select(CourseQuestion).where(
        CourseQuestion.tenant_id == tenant_id,
        CourseQuestion.content_hash.in_(hashes),
    ))).scalars().all()
    by_hash = {row.content_hash: row for row in rows}
    return [
        {
            "seq": q.seq, "qtype": q.qtype, "code": "QUESTION_UNREVIEWED",
            "message": "题库原题未通过审核或已隔离，请换题后发布",
        }
        for q in questions
        if (source := by_hash.get(q.content_hash)) is not None
        and (source.status != "published" or source.source_tag == "quarantine")
    ]


async def _frozen_violations(db, tenant_id: str, questions) -> list:
    """发布前拦「标答已被冻结」的题（M2-10 / G3）。

    冻结的语义是「这道题的标答有问题，正在复核」。这种题**不该再发给学生**：
    学生的成绩页会直接展示标答，错的答案会教出错的数学；而且它判不了分，
    发下去只是把风险转给一线老师。
    """
    reasons = await guard.frozen_reasons(db, tenant_id or "", questions)
    out = []
    for q in questions:
        if guard.reason_for(reasons, q):
            out.append(
                {
                    "seq": q.seq,
                    "qtype": q.qtype,
                    "code": "QUESTION_FROZEN",
                    "message": (
                        "该题标答因反复改判被冻结（标答大概率有误）："
                        "请先修正答案后解冻，或换一道题再发布"
                    ),
                }
            )
    return out


# ============================================================== M2-9 答案双源校验
def _check_out(r) -> AnswerCheckOut:
    return AnswerCheckOut(
        seq=r.seq,
        qtype=r.qtype or "",
        answer=r.answer,
        status=r.status,
        status_label=answer_status_label(r.status),
        secondary_source=r.secondary_source,
        secondary_answer=r.secondary_answer,
        mechanical_code=r.mechanical_code,
        mechanical_detail=r.mechanical_detail,
        suspect=bool(r.suspect),
        detail=r.detail or "",
        llm_model=r.llm_model,
        llm_answer=r.llm_answer,
        llm_agree=r.llm_agree,
        confirmed_by=r.confirmed_by,
        confirmed_at=_to_iso(r.confirmed_at),
        confirmed_comment=r.confirmed_comment,
        checked_at=_to_iso(r.checked_at),
    )


def _check_list(paper, rows, summary) -> AnswerCheckListOut:
    return AnswerCheckListOut(
        paper_id=paper.id,
        published=bool(paper.published),
        summary=AnswerCheckSummaryOut(**summary),
        items=[_check_out(r) for r in rows],
    )


async def _paper_or_404(db: AsyncSession, paper_id: int, user: Optional[dict] = None) -> ExamPaper:
    """取卷并做租户隔离：别校的卷一律 404（不区分「不存在」与「不是你校的」）。

    ``tenant_id`` 为 NULL 的是迁移前的老卷，允许放行（否则它们连自己学校都读不到）。
    """
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="paper not found")
    if user is not None:
        tenant = user.get("tenant_id") or "school_pilot_01"
        if p.tenant_id and p.tenant_id != tenant:
            raise HTTPException(status_code=404, detail="paper not found")
    return p


@router.get("/papers/{paper_id}/answer-checks", response_model=AnswerCheckListOut)
async def get_answer_checks(
    paper_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """答案校验结果（M2-9 / G1）。

    首次访问且尚无记录时会先跑一遍校验（幂等），教师不用先点按钮才看得到结论。
    之后的读取不会覆盖已落库的教师确认——除非答案被改过。
    """
    p = await _paper_or_404(db, paper_id, user)
    qs = await _load_questions(db, paper_id)
    rows, summary = await verify.summary_of(db, p, qs)
    return _check_list(p, rows, summary)


@router.post("/papers/{paper_id}/answer-checks/run", response_model=AnswerCheckListOut)
async def run_answer_checks(
    paper_id: int,
    llm: bool = False,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """重跑校验。``llm=true`` 时额外让租户模型独立求解一遍，**只作建议**。

    建议写进 ``llm_*`` 字段供教师参考，不改变 status，也不自动改答案——
    把模型当第二来源，等于让两个都会出错的模型互相背书。
    """
    p = await _paper_or_404(db, paper_id, user)
    qs = await _load_questions(db, paper_id)
    rows, summary = await verify.summary_of(db, p, qs, refresh=True, llm=llm)
    return _check_list(p, rows, summary)


@router.post("/papers/{paper_id}/answer-checks/confirm", response_model=AnswerCheckListOut)
async def confirm_answer_checks(
    paper_id: int,
    body: AnswerCheckConfirmRequest,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """教师确认（可顺手修正答案）。

    ``seqs`` 为空 = 本卷所有待确认/冲突题。修正答案仅在**未发布**时允许：
    已下发的卷子改答案会让历史成绩的判据变化（学生结果页的「正确答案」也会变），
    那是改分事故而不是修答案。
    """
    p = await _paper_or_404(db, paper_id, user)
    qs = await _load_questions(db, paper_id)
    if not qs:
        raise HTTPException(status_code=409, detail="卷内没有题目，先组卷")
    try:
        rows = await verify.confirm(
            db,
            p,
            qs,
            seqs=body.seqs or None,
            actor=user.get("user_id"),
            comment=body.comment,
            answers=body.answers or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    summary = verify.summarize(rows)
    return _check_list(p, rows, summary)


@router.get("/papers/{paper_id}/validation", response_model=PaperValidationOut)
async def validate_paper_for_publish(
    paper_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """发布前校验（dry-run：**不改试卷**；答案校验记录会顺手刷新落库）。

    前端在发布前调它展示拦截原因与人工量。三层顺序固定：卷型/题型结构 →
    标答冻结（M2-10）→ 答案双源校验（M2-9）。先讲「卷子本身不合法」，再讲
    「这道题的答案正在复核」，最后才是「答案没人验证过」——教师修起来有先后。
    """
    p = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == paper_id))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="paper not found")
    qs = await _load_questions(db, paper_id)
    report = validate_paper(p.paper_type, qs)
    data = report.as_dict()
    problems = list(data["violations"])
    unreviewed = await _unreviewed_violations(db, p.tenant_id, qs)
    if unreviewed:
        problems += unreviewed
    frozen = await _frozen_violations(db, p.tenant_id, qs)
    if frozen:
        problems += frozen
    _, unchecked = await verify.gate(db, p, qs)
    if unchecked:
        problems += unchecked
    if problems:
        data["ok"] = False
        data["violations"] = problems
        bits = []
        if frozen:
            bits.append(f"{len(frozen)} 道冻结题")
        if unchecked:
            bits.append(f"{len(unchecked)} 道答案未通过校验")
        tail = f"（含 {'、'.join(bits)}）" if bits else ""
        data["note"] = f"不可发布：{len(problems)} 处问题待修复{tail}"
    return PaperValidationOut(paper_id=p.id, **data)


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

    qs = await _load_questions(db, paper_id)
    report = validate_paper(p.paper_type, qs)
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

    unreviewed = await _unreviewed_violations(db, p.tenant_id, qs)
    if unreviewed:
        raise HTTPException(status_code=409, detail={
            "code": "QUESTION_UNREVIEWED",
            "message": "试卷含未审核或已隔离题目，请换题后发布",
            "paper_id": p.id,
            "violations": unreviewed,
        })

    # M2-10：结构校验过完再查冻结 —— 先把「卷子本身有问题」说清楚，再谈标答争议
    frozen = await _frozen_violations(db, p.tenant_id, qs)
    if frozen:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "QUESTION_FROZEN",
                "message": (
                    f"{len(frozen)} 道题的标答因反复改判被冻结（标答大概率有误），"
                    "修正答案并解冻后才能发布"
                ),
                "paper_id": p.id,
                "violations": frozen,
            },
        )

    # M2-9 / G1：最后一道闸 —— 答案是判分唯一依据，没验证过的答案不许发给学生。
    # 顺序上必须排在结构校验和冻结之后：结构错、标答冻结都是更硬的错，先修那些。
    rows, unchecked = await verify.gate(db, p, qs)
    if unchecked:
        summary = verify.summarize(rows)
        # 有「双源冲突」时码用 ANSWER_CONFLICT：冲突是「两个来源打架」，
        # 与「压根没人验证过」是两种不同的处理动作，前端要能分开提示。
        code = "ANSWER_CONFLICT" if summary["conflict"] else "ANSWER_UNVERIFIED"
        raise HTTPException(
            status_code=409,
            detail={
                "code": code,
                "message": (
                    f"{len(unchecked)} 道题的答案未通过双源校验（"
                    f"冲突 {summary['conflict']} 道 / 待确认 {summary['unverified']} 道）。"
                    "请在「答案校验」里逐题确认（可顺手修正答案）后再发布"
                ),
                "paper_id": p.id,
                "violations": unchecked,
                "summary": summary,
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

    # M2-10 判分护栏：这道题的标答若已因反复改判被冻结，**所有题一律转人工**，
    # 不再自动判。一次批量查询覆盖整卷，不在循环里逐题查库。
    # 租户口径与开单一致（工单 tenant_id 用同一个表达式），否则冻结记录查不到。
    frozen = await guard.frozen_reasons(
        db, user.get("tenant_id") or a.tenant_id or "", qs
    )

    items = []
    correct_count = 0
    manual = False
    for q in qs:
        submitted = ans_by_seq.get(q.seq)
        stu = (submitted.student_answer or "") if submitted else ""
        blanks = submitted.blanks if submitted else None
        it = _grade_one(q, stu, blanks, frozen_reason=guard.reason_for(frozen, q))
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
            grading_source=it.get("source") or M_SOURCE_UNKNOWN,
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

    questions_by_id = {q.id: q for q in qs}
    for _, obj in item_objs:
        if obj.correct is False:
            await wrongbook.sync_exam_wrong_question(
                db, student_id, obj, questions_by_id[obj.paper_question_id]
            )

    await db.commit()
    await db.refresh(sub)
    return await _result_out(db, sub)


def _grade_one(
    q: ExamPaperQuestion,
    student_answer: str,
    blanks: Optional[list] = None,
    frozen_reason: Optional[str] = None,
) -> dict:
    """对单题做确定性判分（M1-4 题型→判分路由，M2-3 填空三态）。

    - ``choice``：``choice_equivalent``（0 token，单选/多选、连写答案均支持）
    - ``blank``：``blank_judge`` **三态** —— ``auto`` 直接给对/错；``manual``
      （含变量/符号函数、多空空数不符）转人工，**绝不硬判错**
    - ``subjective``：直接人工工单，不调 LLM

    红线：解析不了的结构一律转人工。成绩判错不可逆，转人工只是多一道手续。

    ``frozen_reason``（M2-10）：该题标答已被判分护栏冻结时传入，**先于一切判分
    直接转人工**。这是护栏唯一真正有用的地方——只发告警不拦住判分，等于继续
    按已知有误的标答批量判错。
    """
    # 逐空提交优先；未逐空提交时拼回可展示形态（结果页要能看懂学生答了什么）
    display = student_answer
    if q.qtype == QTYPE_BLANK:
        parts = [str(x or "").strip() for x in (blanks or [])]
        display = student_answer or ("；".join(parts) if parts else "")

    if frozen_reason:
        return _item(
            q, display, correct=None, partial=False,
            status="manual_pending", reason=frozen_reason,
            source=M_SOURCE_FROZEN,
        )

    if q.qtype == QTYPE_CHOICE:
        opt_map = _options_to_map(q.options)
        res = choice_equivalent(student_answer, q.answer, multi=q.multi, options=opt_map)
        if res is True:
            return _item(
                q, student_answer, correct=True, partial=False,
                status="auto", source=M_SOURCE_CHOICE,
            )
        if res == "partial":  # 多选漏选但所选皆正确（MVP 标记不计分）
            return _item(
                q, student_answer, correct=False, partial=True,
                status="auto", source=M_SOURCE_CHOICE,
            )
        return _item(
            q, student_answer, correct=False, partial=False,
            status="auto", source=M_SOURCE_CHOICE,
        )

    if q.qtype == QTYPE_BLANK:
        v = blank_judge(
            display, q.answer or "", blanks=blanks or None, values=_values_of(q)
        )
        if v["status"] == "manual":
            return _item(
                q, display, correct=None, partial=False,
                status="manual_pending", reason=v["reason"],
                source=v.get("source") or M_SOURCE_UNKNOWN,
            )
        return _item(
            q, display, correct=bool(v["correct"]), partial=False,
            status="auto", reason=v["reason"],
            source=v.get("source") or M_SOURCE_UNKNOWN,
        )

    return _item(
        q, student_answer, correct=None, partial=False,
        status="manual_pending", reason="解答题需人工批改",
        source=M_SOURCE_SUBJECTIVE,
    )


def _item(
    q, student_answer, *, correct, partial, status, reason=None, source=""
) -> dict:
    """逐题判分结果。

    ``source``（M2-7）是判据编码，随 item 落库（``grading_source``），指标面板
    据此出「判据覆盖率 / 来源分布」。它必须与结果同时产生——事后再算会把教师
    之后的改动作（追加答案值、修正标答）算进「当时为什么这么判」。
    """
    return {
        "paper_question_id": q.id,
        "seq": q.seq,
        "qtype": q.qtype,
        "student_answer": student_answer,
        "correct": correct,
        "partial": partial,
        "status": status,
        "reason": reason,
        "source": source,
    }


def _manual_reason(it, q, frozen_reason: Optional[str] = None) -> Optional[str]:
    """转人工原因（结果回看用）。

    ``reason`` 不落库（改表成本高于重算）：这里用同一套纯函数重算一次，
    0 token、无副作用，保证「提交当下」与「事后回看」给出的原因一致。

    ``frozen_reason``（M2-10）优先：题目标答被冻结时，真实原因不是「机器判不了
    这道题」，而是「这道题的标答正在复核」——两者对学生是不同的话术。
    """
    if it.status == "manual_reviewed":
        return "老师已复核判定"
    if it.status != "manual_pending":
        return None
    if frozen_reason:
        return frozen_reason
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
    # M2-10：结果回看时也如实说明「这道题标答在复核」，否则学生只看到「待人工」
    # 而不知道全班都待人工 —— 那是标答的问题，不是他的问题
    frozen = (
        await guard.frozen_reasons(
            db, sub.tenant_id or "", list(q_by_id.values())
        )
        if q_by_id
        else {}
    )
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
                reason=_manual_reason(
                    it,
                    q_by_id.get(it.paper_question_id),
                    guard.reason_for(frozen, q_by_id[it.paper_question_id])
                    if it.paper_question_id in q_by_id
                    else None,
                ),
                analysis=(
                    q_by_id[it.paper_question_id].analysis if it.paper_question_id in q_by_id else None
                ),
                teacher_comment=comments.get(it.id),
            )
            for it in items
        ],
    )
