"""M4-2 规则组卷服务薄层：候选过滤 + 配额抽题 + 落库（试卷域）。

纯算法在 ``app.core.compose_engine``；本模块只负责三件事：

1. **候选过滤**（卡面五条硬要求的第 1/2 条）：
   ``published`` + 非 ``quarantine`` + 有答案 + 本租户 + 卷型题型白名单 +
   难度区间；选择题还要过选项/答案合规。
2. **调用引擎**：知识点覆盖优先 -> 难度配比 -> 题源优先级。
3. **落库**：试卷头 + 逐题快照（含分值、answer_values 预展开）。

为什么不把过滤放进纯函数
------------------------
``CourseQuestion`` 是 ORM 对象、过滤要走 SQL（租户/状态/题型是索引列），
把它做成纯函数只能拿全表回内存过滤，线上题库一大就撑不住。所以纯函数
只吃「已过滤的候选集」，SQL 条件留在这里，两边各自可单测。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compose_engine import (
    ComposeError,
    build_quota,
    pick_questions,
    summarize_distribution,
)
from app.core.equiv import expand_answer_values
from app.core.exam_types import (
    QTYPE_BLANK,
    QTYPE_CHOICE,
    QTYPE_SUBJECTIVE,
    course_types_for,
    to_qtype,
    validate_paper,
)
from app.models.course_kb import CourseQuestion
from app.models.ops import ExamPaper, ExamPaperQuestion
from app.models.schemas import ExamPaperCreate

#: 规则组卷的默认分值（教师可在手动出卷页改）。总分 = 各题分值之和。
DEFAULT_SCORES: Dict[str, int] = {
    QTYPE_CHOICE: 5,
    QTYPE_BLANK: 5,
    QTYPE_SUBJECTIVE: 10,
}


def _to_qtype(question: Any) -> Tuple[str, bool]:
    """题库题型 -> (qtype, multi)；未知题型按选择题兜底（候选已按白名单过滤）。"""
    return to_qtype(getattr(question, "type", "") or "") or (QTYPE_CHOICE, False)


def answer_acceptable(question: CourseQuestion) -> bool:
    """有答案 + 答案结构可判（选择题须 A-D 合规，其余题型只要非空）。

    与 ``exam._answer_acceptable`` 同一口径：未知题型不能兜底成选择题去校验
    ``check_choice_answer``，否则「见解析」这类答案永远过不了审核。
    """
    ans = (question.answer or "").strip()
    if not ans:
        return False
    from app.core.exam_types import check_choice_answer

    mapped = to_qtype(question.type)
    if mapped is None:
        return True
    qtype, multi = mapped
    if qtype == QTYPE_CHOICE:
        return check_choice_answer(ans, multi) is None
    return True


def filter_candidates(
    rows: Sequence[CourseQuestion],
    *,
    wanted_kps: Optional[Sequence[str]] = None,
) -> List[CourseQuestion]:
    """内存侧兜底过滤：答案合规 + 可选知识点命中（SQL 已过滤状态/租户/题型）。"""
    wanted = {k for k in (wanted_kps or []) if k}
    out: List[CourseQuestion] = []
    seen: set = set()
    for row in rows or []:
        if not answer_acceptable(row):
            continue
        if wanted and not wanted.intersection(set(row.knowledge_points or [])):
            continue
        # 同卷 hash 去重放在引擎里做（跨知识点共享去重），这里只清掉明显的空 hash 重复
        h = (row.content_hash or "").strip()
        if h and h in seen:
            continue
        if h:
            seen.add(h)
        out.append(row)
    return out


def normalize_difficulty_mix(mix: Optional[Mapping[Any, Any]]) -> Dict[int, int]:
    """``{"3": 4, "5": 1}`` -> ``{3: 4, 5: 1}``，非法键值直接忽略。

    JSON 请求体的对象键一定是字符串；不归一会让 ``pick_questions`` 里
    ``str(difficulty) == str(diff)`` 之外的比较全部落空，难度配比静默失效。
    """
    out: Dict[int, int] = {}
    for key, value in (mix or {}).items():
        try:
            k = int(key)
            v = int(value or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[k] = out.get(k, 0) + v
    return out


def _score_for(qtype: str, score: Optional[float]) -> float:
    if score is not None:
        return float(score)
    return float(DEFAULT_SCORES.get(qtype, 5))


async def load_candidates(
    db: AsyncSession,
    *,
    tenant_id: str,
    grade: str,
    subject: str,
    paper_type: str,
    difficulty_min: Optional[int] = None,
    difficulty_max: Optional[int] = None,
) -> List[CourseQuestion]:
    """按卡面第 1/2 条从题库取候选（SQL 侧过滤，避免全表回内存）。"""
    course_types = course_types_for(paper_type)
    if not course_types:
        return []
    stmt = select(CourseQuestion).where(
        CourseQuestion.subject == subject,
        CourseQuestion.grade == grade,
        CourseQuestion.type.in_(course_types),
        CourseQuestion.tenant_id == tenant_id,
        CourseQuestion.answer.isnot(None),
        CourseQuestion.status == "published",
        (CourseQuestion.source_tag.is_(None)) | (CourseQuestion.source_tag != "quarantine"),
    )
    if difficulty_min is not None:
        stmt = stmt.where(CourseQuestion.difficulty >= int(difficulty_min))
    if difficulty_max is not None:
        stmt = stmt.where(CourseQuestion.difficulty <= int(difficulty_max))
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


def _snapshot(
    paper_id: int,
    seq: int,
    row: CourseQuestion,
    *,
    score: Optional[float] = None,
) -> ExamPaperQuestion:
    qtype, multi = _to_qtype(row)
    answer = (row.answer or "").strip()
    return ExamPaperQuestion(
        paper_id=paper_id,
        seq=seq,
        question_id=row.id,
        content_hash=row.content_hash,
        score=_score_for(qtype, score),
        qtype=qtype,
        multi=multi,
        stem=row.stem,
        options=row.options,
        answer=answer,
        analysis=row.analysis,
        difficulty=row.difficulty,
        knowledge_points=row.knowledge_points or [],
        # M2-4：标答预展开成可接受答案值集合，判分时命中即判对
        answer_values=expand_answer_values(answer) or None,
    )


async def create_rule_paper(
    db: AsyncSession,
    body: ExamPaperCreate,
    *,
    tenant_id: str,
    user_id: Optional[str] = None,
) -> Tuple[ExamPaper, Dict[str, Any]]:
    """规则组卷：抽题 -> 建卷 -> 逐题快照（调用方负责 commit）。

    返回 ``(paper, report)``；``report`` 是实际分布与缺口说明，教师端要能看到
    「难度 4 题源不足（需 2，实际 1）」这类信息，而不是拿到一张悄悄缩水的卷。
    """
    mix = normalize_difficulty_mix(body.difficulty_mix)
    count = int(body.count or 0)
    if mix:
        # 给了难度配比就以配比总量为准：两边不一致会让「抽 10 题」变成 8 题，
        # 且教师看不出是哪个参数生效，直接报参数冲突。
        mix_total = sum(mix.values())
        if mix_total != count:
            raise ComposeError(
                f"参数冲突：难度配比合计 {mix_total} 题，与 count={count} 不一致"
            )

    rows = await load_candidates(
        db,
        tenant_id=tenant_id,
        grade=body.grade,
        subject=body.subject,
        paper_type=body.paper_type,
        difficulty_min=body.difficulty_min,
        difficulty_max=body.difficulty_max,
    )
    cand = filter_candidates(rows, wanted_kps=body.knowledge_points)
    if not cand:
        raise ComposeError(
            f"题源不足：{body.grade}·{body.subject} 在过滤条件（已发布/非隔离/有答案/"
            f"难度区间）下没有任何可用题"
        )

    quota = build_quota(cand, body.knowledge_points, count)
    picked = pick_questions(cand, quota, difficulty_mix=mix or None)

    if len(picked) < count:
        raise ComposeError(
            f"题源不足：可用题仅 {len(picked)} 道（需 {count} 道）。"
            "请降低题量、放宽知识点/难度筛选或改用其他卷型",
            shortfall=[f"总量 {len(picked)}/{count}"],
        )

    distribution = summarize_distribution(picked, mix or None)
    # 难度抽不满是软失败：总量够、知识点覆盖够，只是配比有偏差 -> 出卷并回报
    warnings = list(distribution.get("shortfall") or [])

    paper = ExamPaper(
        tenant_id=tenant_id,
        title=body.title or (
            f"{body.grade}·{body.subject}·{body.paper_type}"
            f"（{len(picked)}题）"
        ),
        paper_type=body.paper_type,
        grade=body.grade,
        subject=body.subject,
        knowledge_points=body.knowledge_points
        or sorted({kp for r in picked for kp in (r.knowledge_points or [])}),
        created_by=user_id,
        version=1,
        source="rule",
        published=False,
    )
    db.add(paper)
    await db.flush()  # 取 paper.id

    questions = [
        _snapshot(paper.id, seq, row) for seq, row in enumerate(picked, start=1)
    ]
    for q in questions:
        db.add(q)

    diffs = [r.difficulty for r in picked if r.difficulty]
    paper.difficulty_avg = round(sum(diffs) / len(diffs)) if diffs else None

    report = {
        **distribution,
        "warnings": warnings,
        "quota": quota,
        "total_score": sum(float(q.score or 0) for q in questions),
    }
    validate = validate_paper(paper.paper_type, questions)
    report["validation"] = validate.as_dict()
    return paper, report