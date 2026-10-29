"""判分护栏落库与流转（M2-10 / G3）。

纯规则在 ``app/core/grade_guard.py``，这里只做三件事：

1. ``register_flip``：教师改判后累计计数，够阈值就冻结并落告警；
2. ``frozen_reasons``：判分前批量查「这道题是否已冻结」——**一次查询覆盖整卷**，
   不在循环里逐题查库；
3. ``release_freeze``：教师复核完解冻，计数清零，可复发。

为什么冻结必须在判分入口生效
----------------------------
护栏的价值不在于「生成一条告警」，而在于**立刻停止继续判错**。冻结后同一标答
的新提交一律转人工：宁可多花老师的几分钟，也不要再产出一批稳定判错的成绩。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.grade_guard import (
    ALERT_FREEZE,
    ALERT_REFREEZE,
    ALERT_RELEASE,
    STATUS_FROZEN,
    STATUS_RELEASED,
    STATUS_WATCHING,
    answer_fingerprint,
    counts_as_flip,
    freeze_message,
    threshold as freeze_threshold,
)
from app.models.ops import (
    ExamAnswerFreeze,
    ExamGradingAlert,
    ExamPaper,
    ExamPaperQuestion,
    ExamSubmissionItem,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dedup(items: Iterable[str]) -> list:
    out = []
    for x in items or []:
        if x and x not in out:
            out.append(x)
    return out


def _key(question) -> Optional[Tuple[str, str]]:
    """题目的护栏键 ``(content_hash, 标答指纹)``；缺任一则不可跨卷冻结。"""
    ch = getattr(question, "content_hash", None)
    if not ch:
        return None
    return ch, answer_fingerprint(getattr(question, "answer", "") or "")


async def get_freeze(
    db: AsyncSession, tenant_id: str, content_hash: str, fingerprint: str
) -> Optional[ExamAnswerFreeze]:
    return (
        await db.execute(
            select(ExamAnswerFreeze).where(
                ExamAnswerFreeze.tenant_id == tenant_id,
                ExamAnswerFreeze.content_hash == content_hash,
                ExamAnswerFreeze.answer_fingerprint == fingerprint,
            )
        )
    ).scalar_one_or_none()


async def frozen_reasons(
    db: AsyncSession, tenant_id: str, questions: Sequence
) -> Dict[Tuple[str, str], str]:
    """批量查整卷里哪些题已被冻结：``{(content_hash, 指纹): 提示语}``。

    只返回 ``status='frozen'`` 的记录。已解冻（released）的题恢复自动判分，
    哪怕它历史上被冻过——解冻是教师明确表态「这题可以继续机判」。
    """
    pairs = {k for k in (_key(q) for q in questions) if k}
    if not pairs:
        return {}
    hashes = {ch for ch, _ in pairs}
    rows = (
        await db.execute(
            select(ExamAnswerFreeze).where(
                ExamAnswerFreeze.tenant_id == tenant_id,
                ExamAnswerFreeze.content_hash.in_(hashes),
                ExamAnswerFreeze.status == STATUS_FROZEN,
            )
        )
    ).scalars().all()
    from app.core.grade_guard import student_frozen_reason

    reason = student_frozen_reason()
    return {
        (r.content_hash, r.answer_fingerprint): reason
        for r in rows
        if (r.content_hash, r.answer_fingerprint) in pairs
    }


def reason_for(mapping: Dict[Tuple[str, str], str], question) -> Optional[str]:
    """取该题的冻结提示语（未冻结返回 None）。"""
    k = _key(question)
    if not k:
        return None
    return mapping.get(k)


async def affected_count(
    db: AsyncSession, tenant_id: str, content_hash: str, fingerprint: str
) -> int:
    """影响面：已按该标答**自动判为错**的提交数。

    实现说明：标答指纹只能在 Python 侧比对（归一化是 Python 函数），所以先按
    content_hash 取到候选题行，再用同一套指纹筛出真正同答案的那些，最后计数。
    题量级很小（一道题在几张卷里出现），不做过度优化。

    **不做自动改判**：静默修改已经发给学生的成绩，比判错更难解释。影响面只用于
    让老师知道该不该回头复核。
    """
    qrows = (
        await db.execute(
            select(ExamPaperQuestion.id, ExamPaperQuestion.answer).where(
                ExamPaperQuestion.content_hash == content_hash
            )
        )
    ).all()
    qids = [qid for qid, ans in qrows if answer_fingerprint(ans or "") == fingerprint]
    if not qids:
        return 0
    stmt = (
        select(func.count())
        .select_from(ExamSubmissionItem)
        .join(
            ExamPaperQuestion,
            ExamPaperQuestion.id == ExamSubmissionItem.paper_question_id,
        )
        .join(ExamPaper, ExamPaper.id == ExamPaperQuestion.paper_id)
        .where(
            ExamSubmissionItem.paper_question_id.in_(qids),
            ExamSubmissionItem.status == "auto",
            ExamSubmissionItem.correct.is_(False),
        )
    )
    # 老卷 tenant_id 可能为空，两种都算本租户的（不为空时严格按租户）
    stmt = stmt.where(
        or_(ExamPaper.tenant_id == tenant_id, ExamPaper.tenant_id.is_(None))
    )
    return (await db.execute(stmt)).scalar() or 0


async def register_flip(
    db: AsyncSession,
    *,
    tenant_id: str,
    question,
    before: Tuple[Optional[bool], Optional[bool]],
    after: Tuple[Optional[bool], Optional[bool]],
    was_reviewed: bool,
    actor_id: Optional[str] = None,
    paper_id: Optional[int] = None,
) -> Optional[ExamAnswerFreeze]:
    """记一次**有效改判**；够阈值则冻结该题并落告警。

    返回护栏记录（``status`` 可能是 watching / frozen）；不可计（首次判定、
    判定没变、题目缺 ``content_hash``）时返回 ``None``。

    两个不计的红线，缺一个护栏就会误伤：

    - ``was_reviewed=False``（首次判定）不计——转人工的题本来就得老师从无到有
      判一次，算成改判的话，任何填空题三个学生就冻结；
    - 判定没变（重复保存/补评语）不计——否则三次双击撞破阈值。
    """
    if not counts_as_flip(was_reviewed, before, after):
        return None
    key = _key(question)
    if not key:
        return None
    content_hash, fp = key
    now = _utcnow()
    row = await get_freeze(db, tenant_id, content_hash, fp)

    revived = False
    if row is None:
        row = ExamAnswerFreeze(
            tenant_id=tenant_id,
            content_hash=content_hash,
            answer_fingerprint=fp,
            answer=(getattr(question, "answer", "") or "")[:4000],
            paper_id=paper_id,
            paper_question_id=getattr(question, "id", None),
            seq=getattr(question, "seq", None),
            qtype=getattr(question, "qtype", None),
            flips=1,
            threshold=freeze_threshold(),
            status=STATUS_WATCHING,
            actors=_dedup([actor_id]),
            affected_count=0,
            release_count=0,
        )
        db.add(row)
        await db.flush()
    else:
        if row.status == STATUS_RELEASED:
            # 解冻后再次改判：重新从 1 计，但 release_count 保留（复发次数是质量指标）
            revived = True
            row.flips = 1
            row.status = STATUS_WATCHING
            row.released_at = None
            row.released_by = None
            row.release_comment = None
        elif row.status == STATUS_FROZEN:
            # 已冻结的题仍在被改判：只涨计数，**不重复告警**
            # （同一条告警一天推十遍，老师会直接无视护栏）
            row.flips = (row.flips or 0) + 1
        else:
            row.flips = (row.flips or 0) + 1
            row.status = STATUS_WATCHING
        row.acked_by = None
        row.acked_at = None
        row.actors = _dedup(list(row.actors or []) + [actor_id])
        row.threshold = row.threshold or freeze_threshold()
        row.answer = (getattr(question, "answer", "") or "")[:4000]
        row.paper_id = paper_id or row.paper_id
        row.paper_question_id = getattr(question, "id", None) or row.paper_question_id
        row.updated_at = now

    thr = row.threshold or freeze_threshold()
    if (row.flips or 0) >= thr and row.status != STATUS_FROZEN:
        row.status = STATUS_FROZEN
        row.frozen_at = now
        row.updated_at = now
        row.affected_count = await affected_count(db, tenant_id, content_hash, fp)
        refreeze = revived or (row.release_count or 0) > 0
        db.add(
            ExamGradingAlert(
                tenant_id=tenant_id,
                kind=ALERT_REFREEZE if refreeze else ALERT_FREEZE,
                freeze_id=row.id,
                paper_id=row.paper_id,
                paper_question_id=row.paper_question_id,
                seq=row.seq,
                content_hash=content_hash,
                answer=row.answer,
                flips=row.flips,
                threshold=thr,
                affected_count=row.affected_count,
                message=freeze_message(row.flips, thr, refreeze=refreeze),
                detail={
                    "actors": list(row.actors or []),
                    "qtype": row.qtype,
                    "answer_fingerprint": fp,
                },
                actor_id=actor_id,
            )
        )
        await db.flush()
    return row


async def release_freeze(
    db: AsyncSession, freeze: ExamAnswerFreeze, user: dict, comment: str
) -> ExamAnswerFreeze:
    """教师复核完成 → 解冻：恢复自动判分，计数清零。

    解冻不删行：release_count 与 alert 流水共同回答「这道题反复出过几次问题」，
    这是判分质量随时间改善的证据，删了就看不到了。
    """
    now = _utcnow()
    freeze.status = STATUS_RELEASED
    freeze.released_at = now
    freeze.released_by = user.get("user_id")
    freeze.release_comment = comment
    freeze.release_count = (freeze.release_count or 0) + 1
    freeze.flips = 0
    freeze.acked_by = None
    freeze.acked_at = None
    freeze.updated_at = now
    db.add(
        ExamGradingAlert(
            tenant_id=freeze.tenant_id,
            kind=ALERT_RELEASE,
            freeze_id=freeze.id,
            paper_id=freeze.paper_id,
            paper_question_id=freeze.paper_question_id,
            seq=freeze.seq,
            content_hash=freeze.content_hash,
            answer=freeze.answer,
            flips=0,
            threshold=freeze.threshold,
            affected_count=freeze.affected_count,
            message=f"教师解冻，恢复自动判分：{comment}",
            detail={"release_count": freeze.release_count},
            actor_id=user.get("user_id"),
        )
    )
    await db.flush()
    return freeze


async def list_freezes(
    db: AsyncSession,
    tenant_id: str,
    status: Optional[str] = "frozen",
    page: int = 1,
    page_size: int = 20,
) -> Tuple[list, dict, int]:
    """护栏列表：默认只看 frozen（= 待处理的告警），可切 released / watching / all。"""
    base = [ExamAnswerFreeze.tenant_id == tenant_id]
    cond = list(base)
    if status and status != "all":
        cond.append(ExamAnswerFreeze.status == status)
    counts = {STATUS_FROZEN: 0, STATUS_WATCHING: 0, STATUS_RELEASED: 0}
    for st, cnt in (
        await db.execute(
            select(ExamAnswerFreeze.status, func.count())
            .where(*base)
            .group_by(ExamAnswerFreeze.status)
        )
    ).all():
        counts[st] = cnt
    total = (
        await db.execute(
            select(func.count()).select_from(ExamAnswerFreeze).where(*cond)
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(ExamAnswerFreeze)
            .where(*cond)
            # 未读的排前面：老师一打开就先看到没人处理过的
            .order_by(
                ExamAnswerFreeze.acked_at.is_(None).desc(),
                ExamAnswerFreeze.frozen_at.desc().nullslast(),
                ExamAnswerFreeze.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return list(rows), counts, total
