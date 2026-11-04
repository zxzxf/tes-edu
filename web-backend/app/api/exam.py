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

import logging
import random
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.answer_verify import status_label as answer_status_label
from app.core.compose_engine import ComposeError
from app.core.config import settings
from app.core.publish_guard import missing_answers, missing_scores, sample_report
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
    WrongQuestion,
)
from app.models.schemas import (
    AnswerCheckConfirmRequest,
    AnswerCheckListOut,
    AnswerCheckOut,
    AnswerCheckSummaryOut,
    AssignmentCreate,
    ManualPaperCreate,
    AssignmentOut,
    ExamPaperCreate,
    ExamPaperOut,
    ExamPaperQuestionOut,
    ExamResult,
    ExamResultItem,
    PaperValidationOut,
    StudentExamBundle,
    StudentExamListItem,
    StudentQuestionView,
    SubmitExamRequest,
)
from app.services import answer_verify as verify
from app.services import drill_link
from app.services import grading_guard as guard
from app.services import paper_compose
from app.services import student_profile
from app.services import wrongbook
from app.services.student_profile import DEFAULT_GRADE, grade_matches

log = logging.getLogger(__name__)

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
    """审核放行门槛：选择题须 A-D 合规；其余题型只要求答案非空。

    未知题型（如「实验」「判断题」等不在 ``COURSE_TYPE_TO_QTYPE`` 内的）不能像
    组卷侧那样兜底成选择题——否则 ``check_choice_answer`` 必然拒掉「见解析」这类
    非选项答案，导致这些题**永远无法通过审核**（2026-10-30 线上冒烟发现）。
    """
    ans = (cq.answer or "").strip()
    if not ans:
        return False
    mapped = to_qtype(cq.type)
    if mapped is None:
        return True
    qtype, multi = mapped
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
    """M4-2 规则组卷：从 course_kb 按配额抽题成卷（答案随卷快照）。

    过滤条件与权重全部在 ``app.services.paper_compose`` / ``app.core.compose_engine``：
    ``published`` + 非 quarantine + 有答案 + 本租户 + 卷型题型白名单 + 难度区间；
    知识点覆盖优先 -> 难度配比 -> 题源优先级（teacher > import > crawl > llm_generated）。
    题源不足直接 409 并说明缺哪个维度，**不静默凑卷**。
    """
    tenant_id = user.get("tenant_id") or "school_pilot_01"
    if not is_known_paper_type(body.paper_type):
        raise HTTPException(status_code=400, detail=f"未知卷型 {body.paper_type!r}")
    if not course_types_for(body.paper_type):
        raise HTTPException(status_code=400, detail=f"卷型 {body.paper_type!r} 无对应题型")

    try:
        paper, report = await paper_compose.create_rule_paper(
            db, body, tenant_id=tenant_id, user_id=user.get("user_id")
        )
    except ComposeError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMPOSE_SOURCE_SHORT",
                "message": exc.message,
                "missing_kps": exc.missing_kps,
                "shortfall": exc.shortfall,
            },
        ) from exc

    await db.commit()
    await db.refresh(paper)
    out = await _paper_out(db, paper)
    # 实际分布与缺口说明（卡面第 5 条）：教师端要能看到「难度 4 不足」这类偏差
    out.compose_report = report
    return out


async def _load_course_questions(
    db: AsyncSession, ids, tenant_id: str
) -> dict:
    """批量取题库行，只允许本租户（手动出卷不得引用别校题目）。"""
    wanted = [int(i) for i in (ids or [])]
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(CourseQuestion).where(
                CourseQuestion.id.in_(wanted),
                CourseQuestion.tenant_id == tenant_id,
            )
        )
    ).scalars().all()
    return {r.id: r for r in rows}


class LlmPaperCreate(BaseModel):
    """M4-3 LLM 辅助组卷请求：按主题出草稿题（教师确认后才发布）。"""

    topic: str = Field(..., min_length=1, max_length=200)
    grade: str = "高三"
    subject: str = "数学"
    paper_type: Literal["pure_choice", "pure_blank", "mixed"] = "pure_choice"
    count: int = Field(default=10, ge=1, le=30)
    difficulty_mix: dict = {}


@router.post("/papers/llm-generate", status_code=201)
async def llm_generate_paper(
    body: LlmPaperCreate,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M4-3：按主题调租户模型出草稿题（连同草稿卷），**一律不发布**。

    题目落 ``course_kb(status=draft, source_tag=llm_generated)``；
    教师须走 M3-7 审核转 ``published`` 后，才可能被规则组卷抽到。
    即使这里返回了 ``paper_id``，该卷也是草稿：发布闸门会因题目未审核而 409。

    模型不可用（未配置/403/429/超时/输出不合规）时返回 503/422 + 可读原因，
    **绝不回落到模板题冒充生成**。
    """
    from app.services import llm_paper as llm_paper_svc

    tenant_id = _tenant_of(user)
    if not is_known_paper_type(body.paper_type):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "BAD_PAPER_TYPE",
                "message": f"未知卷型 {body.paper_type}",
                "allowed": list(PAPER_TYPE_LABEL),
            },
        )
    try:
        result = await llm_paper_svc.generate_drafts(
            db,
            tenant_id=tenant_id,
            topic=body.topic,
            grade=body.grade,
            subject=body.subject,
            paper_type=body.paper_type,
            count=body.count,
            difficulty_mix=body.difficulty_mix,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "LLM_OUTPUT_INVALID", "message": str(exc)}
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "LLM_UNAVAILABLE", "message": str(exc)}
        ) from exc
    await db.commit()

    if result["created"] == 0:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LLM_NO_VALID_QUESTION",
                "message": "生成结果没有可入库的题（可能都被答案/题型校验拦下）",
                "rejected": result["rejected"],
            },
        )
    return result


@router.post("/papers/manual", response_model=ExamPaperOut, status_code=201)
async def create_manual_paper(
    body: ManualPaperCreate,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M4-1 手动出卷：教师按筛选结果选题 + 配分，保存为草稿卷。

    - 只允许引用**本租户**、``published``、非隔离、有答案的题；
    - 每题可覆盖分值/答案（题库答案有误时不必先改题库，改的是本卷快照）；
    - ``publish=true`` 时走与 ``/publish`` 完全相同的闸门（结构/G1/G2），不绕过。
    """
    tenant_id = user.get("tenant_id") or "school_pilot_01"
    if not is_known_paper_type(body.paper_type):
        raise HTTPException(status_code=400, detail=f"未知卷型 {body.paper_type!r}")
    if not body.questions:
        raise HTTPException(status_code=400, detail="请至少选择 1 道题")

    ids = [it.question_id for it in body.questions]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="同一道题不能重复入卷")

    bank = await _load_course_questions(db, ids, tenant_id)
    missing = [i for i in ids if i not in bank]
    if missing:
        raise HTTPException(
            status_code=404,
            detail={"code": "QUESTION_NOT_FOUND", "message": "题目不存在或不属于本校",
                    "question_ids": missing},
        )

    bad = [
        i for i in ids
        if bank[i].status != "published" or bank[i].source_tag == "quarantine"
        or not (bank[i].answer or "").strip()
    ]
    if bad:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "QUESTION_NOT_PUBLISHED",
                "message": "含未审核/已隔离/无答案题目，不能入卷（请先在题目审核里放行）",
                "question_ids": bad,
            },
        )

    allowed_types = set(course_types_for(body.paper_type))
    wrong_type = [i for i in ids if (bank[i].type or "") not in allowed_types]
    if wrong_type:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "QTYPE_NOT_ALLOWED",
                "message": f"卷型 {body.paper_type} 不包含所选题目类型",
                "question_ids": wrong_type,
            },
        )

    paper = ExamPaper(
        tenant_id=tenant_id,
        title=body.title or f"{body.grade}·{body.subject}·手动组卷（{len(ids)}题）",
        paper_type=body.paper_type,
        grade=body.grade,
        subject=body.subject,
        knowledge_points=body.knowledge_points
        or sorted({kp for i in ids for kp in (bank[i].knowledge_points or [])}),
        created_by=user.get("user_id"),
        version=1,
        source="manual",
        published=False,
    )
    db.add(paper)
    await db.flush()

    questions = []
    for seq, item in enumerate(body.questions, start=1):
        row = bank[item.question_id]
        qtype, multi = _qtype_from_course(row.type)
        answer = (item.answer if item.answer is not None else row.answer or "").strip()
        values = list(item.answer_values or [])
        if not values:
            values = expand_answer_values(answer) or []
        q = ExamPaperQuestion(
            paper_id=paper.id,
            seq=seq,
            question_id=row.id,
            content_hash=row.content_hash,
            # 未配分时留空：发布闸门会拦「未配分」，不替教师猜分
            score=item.score,
            qtype=qtype,
            multi=multi,
            stem=row.stem,
            options=row.options,
            answer=answer,
            analysis=row.analysis,
            difficulty=row.difficulty,
            knowledge_points=row.knowledge_points or [],
            answer_values=values or None,
        )
        db.add(q)
        questions.append(q)

    diffs = [r.difficulty for i in ids for r in [bank[i]] if r.difficulty]
    paper.difficulty_avg = round(sum(diffs) / len(diffs)) if diffs else None

    await db.commit()
    await db.refresh(paper)
    if body.publish:
        return await publish_paper(paper.id, user=user, db=db)
    return await _paper_out(db, paper)


@router.post("/papers/{paper_id}/copy", response_model=ExamPaperOut, status_code=201)
async def copy_paper(
    paper_id: int,
    title: Optional[str] = None,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M4-5 复制/另存：新 id，引用同 question_id/content_hash，快照独立。

    复制出来的卷**一定是草稿**且 version=1：已发布卷的判据不能因为复制被改动；
    原卷 version 自增只用于记录「被另存过」，不影响已下发卷的判分。
    """
    src = await _paper_or_404(db, paper_id, user)
    src_qs = await _load_questions(db, paper_id)

    copy = ExamPaper(
        tenant_id=src.tenant_id,
        title=title or f"{src.title}（副本）",
        paper_type=src.paper_type,
        grade=src.grade,
        subject=src.subject,
        difficulty_avg=src.difficulty_avg,
        knowledge_points=list(src.knowledge_points or []),
        created_by=user.get("user_id"),
        version=1,
        source="copy",
        published=False,
    )
    db.add(copy)
    await db.flush()

    for q in src_qs:
        db.add(
            ExamPaperQuestion(
                paper_id=copy.id,
                seq=q.seq,
                question_id=q.question_id,
                content_hash=q.content_hash,
                score=q.score,
                qtype=q.qtype,
                multi=q.multi,
                stem=q.stem,
                options=q.options,
                answer=q.answer,
                analysis=q.analysis,
                difficulty=q.difficulty,
                knowledge_points=list(q.knowledge_points or []),
                answer_values=list(q.answer_values or []) or None,
            )
        )

    src.version = int(src.version or 1) + 1
    await db.commit()
    await db.refresh(copy)
    return await _paper_out(db, copy)


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
        version=int(paper.version or 1),
        source=paper.source or "manual",
        question_count=len(qs),
        auto_gradeable_count=report.auto_gradeable_count,
        manual_required_count=report.manual_required_count,
        total_score=sum(float(q.score or 0) for q in qs),
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
                question_id=q.question_id,
                score=float(q.score) if q.score is not None else None,
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
    # 标答可能被修正：卷题快照（含 blank_count）与该卷判分结果都要重算
    await invalidate_paper(p.tenant_id or "", paper_id)
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
    # M4-6：无答案/未配分先于结构之外的业务闸门报，教师一眼知道要补什么
    problems += missing_answers(qs)
    problems += missing_scores(qs)
    unreviewed = await _unreviewed_violations(db, p.tenant_id, qs)
    if unreviewed:
        problems += unreviewed
    frozen = await _frozen_violations(db, p.tenant_id, qs)
    if frozen:
        problems += frozen
    _, unchecked = await verify.gate(db, p, qs)
    if unchecked:
        problems += unchecked
    # M4-7 G2：发布前标准样本空跑（正确解/常见错法/等价写法/边界写法）
    g2 = sample_report(qs)
    if not g2["ok"]:
        problems += g2["blocking"]
    if problems:
        data["ok"] = False
        data["violations"] = problems
        bits = []
        if frozen:
            bits.append(f"{len(frozen)} 道冻结题")
        if unchecked:
            bits.append(f"{len(unchecked)} 道答案未通过校验")
        if not g2["ok"]:
            bits.append(f"{len(g2['blocking'])} 处抽样判定异常")
        tail = f"（含 {'、'.join(bits)}）" if bits else ""
        data["note"] = f"不可发布：{len(problems)} 处问题待修复{tail}"
    data["g2"] = g2
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

    # M4-6：无答案 / 未配分是「卷子根本不完整」，排在结构校验之后、审核之前
    no_answer = missing_answers(qs)
    if no_answer:
        raise HTTPException(status_code=409, detail={
            "code": "ANSWER_MISSING",
            "message": f"{len(no_answer)} 道题没有答案，无法判分",
            "paper_id": p.id,
            "violations": no_answer,
        })

    no_score = missing_scores(qs)
    if no_score:
        raise HTTPException(status_code=409, detail={
            "code": "SCORE_MISSING",
            "message": f"{len(no_score)} 道题未配分，无法计算总分",
            "paper_id": p.id,
            "violations": no_score,
        })

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

    # M4-7 G2：发布前标准样本空跑。正确解被判错/常见错法被判对 -> 拦；
    # 「新卷首周强制 100% 抽样」在这一层就已经是 100%，不需要再抽一部分。
    g2 = sample_report(qs)
    if not g2["ok"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "G2_SAMPLE_FAILED",
                "message": (
                    f"{len(g2['blocking'])} 处标准样本判定异常（正确解被判错或"
                    "常见错法被判对），修正标答后再发布"
                ),
                "paper_id": p.id,
                "violations": g2["blocking"],
                "sample": g2["samples"],
            },
        )

    p.published = True
    p.published_at = _utcnow()
    await db.commit()
    # 发布/重新发布可能修正了题面或标答：题干与历史结果缓存一并失效
    await invalidate_paper(p.tenant_id or "", p.id)
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
# ---- 读缓存（Redis 可选）--------------------------------------------------
# key 一律带 tenant_id：Redis 是网关/agent/web 共用的，试卷与成绩属于学校资产，
# 少了这一维就是跨校串数据。缓存 miss / Redis 挂了都照常查 106 的 PG。
def _q_cache_key(tenant_id: str, paper_id: int) -> str:
    return f"exam:v1:q:{tenant_id}:{paper_id}"


def _r_cache_prefix(tenant_id: str, paper_id: int) -> str:
    return f"exam:v1:r:{tenant_id}:{paper_id}:"


def _r_cache_key(tenant_id: str, paper_id: int, submission_id: int) -> str:
    return _r_cache_prefix(tenant_id, paper_id) + str(submission_id)


async def _student_questions(
    db: AsyncSession, paper_id: int, tenant_id: str
) -> list[StudentQuestionView]:
    """卷题快照（发布后冻结）：Redis 命中直接返回，否则查库回填。"""
    key = _q_cache_key(tenant_id, paper_id)
    hit = await cache.get_json(key)
    if isinstance(hit, list):
        try:
            return [StudentQuestionView(**x) for x in hit]
        except Exception as exc:  # noqa: BLE001  脏缓存当 miss 处理
            log.debug("question cache unusable %s: %s", key, exc)
    qs = (
        await db.execute(
            select(ExamPaperQuestion)
            .where(ExamPaperQuestion.paper_id == paper_id)
            .order_by(ExamPaperQuestion.seq)
        )
    ).scalars().all()
    views = [
        StudentQuestionView(
            seq=q.seq,
            qtype=q.qtype,
            multi=q.multi,
            stem=q.stem,
            options=_options_to_list(q.options),
            difficulty=q.difficulty,
            knowledge_points=q.knowledge_points or [],
            blank_count=len(split_blanks(q.answer or "")) if q.qtype == QTYPE_BLANK else 0,
        )
        for q in qs
    ]
    await cache.set_json(
        key, [v.model_dump() for v in views], settings.exam_cache_ttl
    )
    return views


async def _result_cached(
    db: AsyncSession, sub: ExamSubmission, tenant_id: str, paper_id: int
) -> ExamResult:
    """判分结果：结果主体（逐题 + 冻结原因 + 老师评语）整包缓存。

    TTL 只有 5 分钟，且教师改判 / 标答复核会主动 delete_prefix 失效，
    所以学生不会长期看到过期的「待人工」状态。
    """
    key = _r_cache_key(tenant_id, paper_id, sub.id)
    hit = await cache.get_json(key)
    if isinstance(hit, dict):
        try:
            return ExamResult(**hit)
        except Exception as exc:  # noqa: BLE001
            log.debug("result cache unusable %s: %s", key, exc)
    out = await _result_out(db, sub)
    await cache.set_json(key, out.model_dump(), settings.exam_result_cache_ttl)
    return out


async def invalidate_paper(tenant_id: str, paper_id: int) -> None:
    """卷内容/判分口径变了：题干 + 该卷所有学生的结果一起失效。

    由发布卷、标答复核、人工改判调用；无 Redis 时是 no-op。
    """
    await cache.delete(_q_cache_key(tenant_id, paper_id))
    await cache.delete_prefix(_r_cache_prefix(tenant_id, paper_id))


def _tenant_of(user: dict) -> str:
    return user.get("tenant_id") or "school_pilot_01"


def _visible_to(a: ExamAssignment, user: dict, grade: Optional[str] = None) -> bool:
    """学生能否看到这份下发。

    **租户隔离是第一道门**：年级段下发（target_type=grade）没有学生 id，
    只按 `target_grade` 命中的话，别的学校的高三学生也会看到这份卷——
    实测 demo 租户的教师卷和试点校学生的列表会互相串。

    ``grade`` 是学生自己的年级（M0-3 ``student_profile``），为空时按租户默认
    年级兜底。此前这里写死「高三」，高一/高二学生永远看不到卷，而高三学生
    又会收到本租户其它年级的卷——两个方向都是错的。
    """
    tenant = _tenant_of(user)
    if (a.tenant_id or "") != tenant:
        return False
    if a.target_type == "student":
        return a.target_student == user["user_id"]
    return grade_matches(a.target_grade, grade or DEFAULT_GRADE)


async def _assignment_for_student(
    db: AsyncSession, assignment_id: int, user: dict
) -> ExamAssignment:
    """按 (id, 本租户) 取下发；不可见一律 404，不区分"不存在"与"不是你校的"。"""
    a = (
        await db.execute(select(ExamAssignment).where(ExamAssignment.id == assignment_id))
    ).scalar_one_or_none()
    grade = await student_profile.get_grade(
        db, user["user_id"], _tenant_of(user)
    )
    if not a or not _visible_to(a, user, grade):
        raise HTTPException(status_code=404, detail="assignment not found")
    return a


@student_router.get("", response_model=list[StudentExamListItem])
async def list_student_exams(
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """列出该学生可做的试卷（本租户 + 已发布 + 下发到本人/本年级段）。

    ⚠️ **固定 5 条 SQL，与下发条数无关**（N+1 批量化，2026-11-04）。
    库在另一台机器上（137 应用 → 106 数据，跨云公网），每条 SQL 都是一次往返
    （实测 RTT ≈ 17ms）。原实现每份下发跑 3 条（卷、题数、提交），20 份就是
    60 次往返 ≈ 1 秒纯网络等待，叠加隧道抖动后 App 端就是「转圈很久然后超时」。
    这里一次性把卷 / 题数 / 提交记录全取回来在内存里配对——下发再多也只有
    5 条查询（年级 1 + 下发 2 + 卷 1 + 题数 1 + 提交 1）。

    别为了「代码短」退回逐份查询：这个接口是学生打开 App 的第一个请求。
    """
    student_id = user["user_id"]
    tenant_id = _tenant_of(user)
    # M0-3：按学生真实年级取卷；没配档案时用租户默认年级兜底（只读，不落库）
    grade = await student_profile.get_grade(db, student_id, tenant_id)
    grade_rows = (
        await db.execute(
            select(ExamAssignment).where(
                ExamAssignment.tenant_id == tenant_id,
                ExamAssignment.target_type == "grade",
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

    # 先做可见性过滤（纯内存判断，不碰库），只对真正会下发的卷查后续数据
    visible = [a for a in list(grade_rows) + list(stu_rows) if _visible_to(a, user, grade)]
    if not visible:
        return []

    paper_ids = {a.paper_id for a in visible}
    papers = {
        p.id: p
        for p in (
            await db.execute(select(ExamPaper).where(ExamPaper.id.in_(paper_ids)))
        ).scalars().all()
    }
    counts = dict(
        (
            await db.execute(
                select(ExamPaperQuestion.paper_id, func.count())
                .where(ExamPaperQuestion.paper_id.in_(paper_ids))
                .group_by(ExamPaperQuestion.paper_id)
            )
        ).all()
    )
    submitted_ids = {
        row
        for (row,) in (
            await db.execute(
                select(ExamSubmission.assignment_id).where(
                    ExamSubmission.assignment_id.in_([a.id for a in visible]),
                    ExamSubmission.student_id == student_id,
                )
            )
        ).all()
    }

    out = []
    for a in visible:
        p = papers.get(a.paper_id)
        if p is None or not p.published:
            continue
        out.append(
            StudentExamListItem(
                assignment_id=a.id,
                paper_id=a.paper_id,
                title=p.title,
                paper_type=p.paper_type,
                grade=p.grade,
                subject=p.subject,
                question_count=int(counts.get(a.paper_id) or 0),
                due_at=_to_iso(a.due_at),
                submitted=a.id in submitted_ids,
            )
        )
    return out


@student_router.get("/{assignment_id}", response_model=list[StudentQuestionView])
async def get_exam_for_student(
    assignment_id: int,
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """取卷：只返回题干 + 选项（不含答案/解析）。卷题快照走 Redis。"""
    a = await _assignment_for_student(db, assignment_id, user)
    return await _student_questions(db, a.paper_id, _tenant_of(user))


@student_router.get("/{assignment_id}/bundle", response_model=StudentExamBundle)
async def get_exam_bundle(
    assignment_id: int,
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """一次进卷拿全：题干 + 是否已交 + 判分结果（弱网合并接口）。

    App 进卷原本要串行发「取卷」+（已提交时）「取结果」两次，每次都过一遍
    跨境隧道（实测 1.5–2.9s）。这里合并成一次往返；题干/结果各自走 Redis，
    重复进卷也不再重复跨云查库。

    安全口径与两个原接口完全一致：先按 (租户, 学生) 校验下发可见性；
    ``questions`` 不含答案；``result`` 仅在本人已提交时返回。
    """
    a = await _assignment_for_student(db, assignment_id, user)
    tenant = _tenant_of(user)
    questions = await _student_questions(db, a.paper_id, tenant)
    paper = (
        await db.execute(select(ExamPaper).where(ExamPaper.id == a.paper_id))
    ).scalar_one_or_none()
    sub = (
        await db.execute(
            select(ExamSubmission).where(
                ExamSubmission.assignment_id == assignment_id,
                ExamSubmission.student_id == user["user_id"],
            )
        )
    ).scalar_one_or_none()
    result = (
        await _result_cached(db, sub, tenant, sub.paper_id or a.paper_id)
        if sub is not None
        else None
    )
    return StudentExamBundle(
        assignment_id=assignment_id,
        paper_id=a.paper_id,
        title=paper.title if paper else "",
        paper_type=paper.paper_type if paper else "",
        submitted=sub is not None,
        questions=questions,
        result=result,
    )


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
    # 批量同步错题本：把判错的题一次性交给批量接口，避免逐题 select（N 次往返）。
    wrong_pairs = [
        (obj, questions_by_id[obj.paper_question_id])
        for _, obj in item_objs
        if obj.correct is False
    ]
    await wrongbook.sync_exam_wrong_questions(db, student_id, wrong_pairs)

    # M3-6：判错 -> 知识点 -> 背诵卡。按 kp 建/推卡（同 kp 只一张），
    # 失败不影响提交主链路（错题本已经落库，卡可以下次进错题页补）。
    if wrong_pairs:
        try:
            await db.flush()
            wrong_rows = (
                await db.execute(
                    select(WrongQuestion).where(
                        WrongQuestion.student_id == student_id,
                        WrongQuestion.client_id.in_(
                            [f"exam:{obj.id}" for _, obj in wrong_pairs]
                        ),
                        WrongQuestion.deleted_at.is_(None),
                    )
                )
            ).scalars().all()
            await drill_link.on_wrong_exam_items(
                db,
                student_id=student_id,
                tenant_id=user.get("tenant_id") or a.tenant_id,
                wrong_rows=wrong_rows,
            )
        except Exception as exc:  # noqa: BLE001 卡片失败不阻断交卷
            log.warning("drill card link failed: %s", exc)

    await db.commit()
    await db.refresh(sub)
    # 结果首次产生：直接回填缓存，交卷后立刻进结果页不必再跑一遍 5–8 条跨云 SQL
    tenant = user.get("tenant_id") or a.tenant_id or "school_pilot_01"
    key = _r_cache_key(tenant, a.paper_id, sub.id)
    await cache.delete(key)
    result = await _result_out(db, sub)
    await cache.set_json(key, result.model_dump(), settings.exam_result_cache_ttl)
    return result


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
    return await _result_cached(
        db, sub, _tenant_of(user), sub.paper_id or 0
    )


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
