"""判分指标聚合（M2-7）：只负责数数，口径全在 ``app/core/grading_metrics.py``。

四个数据源
----------
* ``exam_submission_items``：逐题判分结果 + ``grading_source``（哪条判据给的结论）
  → 判据覆盖率、人工复核率、来源分布、按卷下钻；
* ``exam_grading_tickets``：人工工单 → 积压、时效分位；
* ``exam_grading_revisions``：批改留痕 → 有效改判数、冤枉数（错改对）；
* ``exam_answer_freeze`` / ``exam_grading_alerts``：护栏 → 冻结 / 复发。

为什么不缓存
------------
面板数据量级是「几千行聚合」，单次查询毫秒级；而缓存会让「刚判定完但面板还是
旧数」成为老师最常遇到的困惑。宁可每次现算。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.grade_guard import STATUS_FROZEN, STATUS_RELEASED, STATUS_WATCHING
from app.core.grading_metrics import (
    SOURCE_UNKNOWN,
    accuracy_metric,
    coverage_metric,
    manual_rate_metric,
    percentile,
    ratio,
    source_rows,
)
from app.models.ops import (
    ExamAnswerFreeze,
    ExamGradingAlert,
    ExamGradingRevision,
    ExamGradingTicket,
    ExamPaper,
    ExamSubmission,
    ExamSubmissionItem,
)

DEFAULT_DAYS = 30
_GRADABLE_QTYPES = ("choice", "blank")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """统一成带时区的时间。

    PG 的 ``timestamptz`` 读出来是 aware，SQLite（单测）读出来是 naive，
    两者相减会抛 ``can't subtract offset-naive and offset-aware datetimes``。
    这里只补时区不改数值：naive 一律按 UTC 解释（写入时就是 UTC）。
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _window_cond(days: Optional[int]):
    """窗口条件。``days is None`` 或 ``<=0`` 表示不限时间（全量口径）。"""
    if not days or days <= 0:
        return None
    return ExamSubmission.submitted_at >= _utcnow() - timedelta(days=days)


async def _item_counts(db: AsyncSession, cond: list) -> dict:
    """按 (题型, 状态) 计数，一次查询供多个指标复用。"""
    rows = (
        await db.execute(
            select(
                ExamSubmissionItem.qtype,
                ExamSubmissionItem.status,
                func.count(),
            )
            .join(ExamSubmission, ExamSubmission.id == ExamSubmissionItem.submission_id)
            .where(*cond)
            .group_by(ExamSubmissionItem.qtype, ExamSubmissionItem.status)
        )
    ).all()

    by_status = {"auto": 0, "manual_pending": 0, "manual_reviewed": 0}
    by_qtype: dict = {}
    gradable_total = 0
    auto_gradable = 0
    total = 0
    for qtype, status, n in rows:
        n = int(n)
        total += n
        by_qtype[qtype] = by_qtype.get(qtype, 0) + n
        by_status[status] = by_status.get(status, 0) + n
        if qtype in _GRADABLE_QTYPES:
            gradable_total += n
            if status == "auto":
                auto_gradable += n
    return {
        "total": total,
        "by_status": by_status,
        "by_qtype": by_qtype,
        "gradable_total": gradable_total,
        "auto_gradable": auto_gradable,
    }


async def _source_counts(db: AsyncSession, cond: list) -> dict:
    """判据来源分布。NULL（加列前的历史行）归 ``unknown``，不猜。"""
    src = func.coalesce(ExamSubmissionItem.grading_source, SOURCE_UNKNOWN)
    rows = (
        await db.execute(
            select(src, func.count())
            .join(ExamSubmission, ExamSubmission.id == ExamSubmissionItem.submission_id)
            .where(*cond)
            .group_by(src)
        )
    ).all()
    return {code: int(n) for code, n in rows}


async def _ticket_stats(db: AsyncSession, tenant_id: str, days: Optional[int]) -> dict:
    """工单：状态计数、最老待办时长、判定时效 P50/P90。"""
    cond = [ExamGradingTicket.tenant_id == tenant_id]
    if days and days > 0:
        cond.append(
            ExamGradingTicket.created_at >= _utcnow() - timedelta(days=days)
        )
    counts = {"pending": 0, "claimed": 0, "reviewed": 0}
    for st, n in (
        await db.execute(
            select(ExamGradingTicket.status, func.count())
            .where(*cond)
            .group_by(ExamGradingTicket.status)
        )
    ).all():
        counts[st] = int(n)

    pending_rows = (
        await db.execute(
            select(ExamGradingTicket.created_at).where(
                *cond, ExamGradingTicket.status.in_(("pending", "claimed"))
            )
        )
    ).scalars().all()
    now = _utcnow()
    stamps = [_aware(dt) for dt in pending_rows if dt is not None]
    oldest_hours = (
        round((now - min(stamps)).total_seconds() / 3600.0, 1) if stamps else None
    )

    # 时效只统计真正判完的：created_at → reviewed_at
    pairs = (
        await db.execute(
            select(ExamGradingTicket.created_at, ExamGradingTicket.reviewed_at).where(
                *cond, ExamGradingTicket.reviewed_at.is_not(None)
            )
        )
    ).all()
    minutes = [
        (rv2 - cr2).total_seconds() / 60.0
        for cr, rv in pairs
        if (cr2 := _aware(cr)) is not None and (rv2 := _aware(rv)) is not None
    ]
    return {
        "counts": counts,
        "oldest_pending_hours": oldest_hours,
        "turnaround_minutes": {
            "p50": percentile(minutes, 50),
            "p90": percentile(minutes, 90),
            "n": len(minutes),
        },
    }


async def _revision_stats(db: AsyncSession, tenant_id: str, days: Optional[int]) -> dict:
    """改判统计。

    有效改判 = ``action='rejudge'`` 且判定真的变了（与 ``grade_guard.counts_as_flip``
    同一口径）。首次判定（``action='judge'``）不算——它是「从无到有」，不是改判。
    """
    cond = [ExamGradingTicket.tenant_id == tenant_id]
    if days and days > 0:
        cond.append(ExamGradingRevision.created_at >= _utcnow() - timedelta(days=days))

    changed = or_(
        ExamGradingRevision.before_correct.is_distinct_from(
            ExamGradingRevision.after_correct
        ),
        ExamGradingRevision.before_partial.is_distinct_from(
            ExamGradingRevision.after_partial
        ),
    )
    base = (
        select(func.count())
        .select_from(ExamGradingRevision)
        .join(
            ExamGradingTicket, ExamGradingTicket.id == ExamGradingRevision.ticket_id
        )
        .where(*cond)
    )
    flips = (
        await db.execute(
            base.where(ExamGradingRevision.action == "rejudge", changed)
        )
    ).scalar() or 0
    # 冤枉：老师把「错」改成「对」。这是最该盯的数，目标是 0。
    wrong_to_right = (
        await db.execute(
            base.where(
                ExamGradingRevision.action == "rejudge",
                ExamGradingRevision.before_correct.is_(False),
                ExamGradingRevision.after_correct.is_(True),
            )
        )
    ).scalar() or 0
    right_to_wrong = (
        await db.execute(
            base.where(
                ExamGradingRevision.action == "rejudge",
                ExamGradingRevision.before_correct.is_(True),
                ExamGradingRevision.after_correct.is_(False),
            )
        )
    ).scalar() or 0
    first_judged = (
        await db.execute(base.where(ExamGradingRevision.action == "judge"))
    ).scalar() or 0
    added_values = (
        await db.execute(base.where(ExamGradingRevision.action == "add_value"))
    ).scalar() or 0
    return {
        "effective_flips": int(flips),
        "wrong_to_right": int(wrong_to_right),
        "right_to_wrong": int(right_to_wrong),
        "first_judged": int(first_judged),
        "added_values": int(added_values),
        # 冤枉率：错改对 / 被判错且被复核的题（分母用「改判总数」更稳，
        # 因为被判错的基数在没有抽样复核时不可知）
        "wrong_to_right_rate": ratio(wrong_to_right, flips),
    }


async def _freeze_stats(db: AsyncSession, tenant_id: str, days: Optional[int]) -> dict:
    counts = {STATUS_FROZEN: 0, STATUS_WATCHING: 0, STATUS_RELEASED: 0}
    for st, n in (
        await db.execute(
            select(ExamAnswerFreeze.status, func.count())
            .where(ExamAnswerFreeze.tenant_id == tenant_id)
            .group_by(ExamAnswerFreeze.status)
        )
    ).all():
        counts[st] = int(n)
    refrozen = (
        await db.execute(
            select(func.count())
            .select_from(ExamAnswerFreeze)
            .where(
                ExamAnswerFreeze.tenant_id == tenant_id,
                ExamAnswerFreeze.release_count > 0,
            )
        )
    ).scalar() or 0

    acond = [ExamGradingAlert.tenant_id == tenant_id]
    if days and days > 0:
        acond.append(ExamGradingAlert.created_at >= _utcnow() - timedelta(days=days))
    alerts = {}
    for kind, n in (
        await db.execute(
            select(ExamGradingAlert.kind, func.count())
            .where(*acond)
            .group_by(ExamGradingAlert.kind)
        )
    ).all():
        alerts[kind] = int(n)
    return {
        "frozen": counts[STATUS_FROZEN],
        "watching": counts[STATUS_WATCHING],
        "released": counts[STATUS_RELEASED],
        "refrozen": int(refrozen),
        "alerts": alerts,
    }


async def _reviewed_auto(db: AsyncSession, tenant_id: str, days: Optional[int]) -> tuple:
    """机器判过的题里，有多少被复核过、其中多少被改判。

    当前恒为 0（机器判完就结束，没有复核入口），但代码按「有数据就出数」写，
    等 M4-7 抽样复核上线不用改这里。
    """
    cond = [
        ExamGradingTicket.tenant_id == tenant_id,
        ExamSubmissionItem.status == "auto",
    ]
    if days and days > 0:
        cond.append(ExamGradingRevision.created_at >= _utcnow() - timedelta(days=days))
    reviewed = (
        await db.execute(
            select(func.count(func.distinct(ExamGradingRevision.submission_item_id)))
            .select_from(ExamGradingRevision)
            .join(
                ExamGradingTicket, ExamGradingTicket.id == ExamGradingRevision.ticket_id
            )
            .join(
                ExamSubmissionItem,
                ExamSubmissionItem.id == ExamGradingRevision.submission_item_id,
            )
            .where(*cond)
        )
    ).scalar() or 0
    overturned = (
        await db.execute(
            select(func.count(func.distinct(ExamGradingRevision.submission_item_id)))
            .select_from(ExamGradingRevision)
            .join(
                ExamGradingTicket, ExamGradingTicket.id == ExamGradingRevision.ticket_id
            )
            .join(
                ExamSubmissionItem,
                ExamSubmissionItem.id == ExamGradingRevision.submission_item_id,
            )
            .where(
                *cond,
                or_(
                    ExamGradingRevision.before_correct.is_distinct_from(
                        ExamGradingRevision.after_correct
                    ),
                    ExamGradingRevision.before_partial.is_distinct_from(
                        ExamGradingRevision.after_partial
                    ),
                ),
            )
        )
    ).scalar() or 0
    return int(reviewed), int(overturned)


async def _by_paper(
    db: AsyncSession, cond: list, tenant_id: str, limit: int = 8
) -> list:
    """按卷下钻：哪张卷在拖低覆盖率。

    只列题量 Top N（默认 8）——面板是用来发现问题的，不是把全部卷搬上来。
    """
    # 用 CASE 求和而不是 CAST(bool)：SQLite（单测）与 PG 都能跑，
    # 且布尔转整的写法在两边行为并不一致。
    gradable_flag = case((ExamSubmissionItem.qtype.in_(_GRADABLE_QTYPES), 1), else_=0)
    auto_flag = case(
        (
            ExamSubmissionItem.qtype.in_(_GRADABLE_QTYPES)
            & (ExamSubmissionItem.status == "auto"),
            1,
        ),
        else_=0,
    )
    manual_flag = case(
        (ExamSubmissionItem.status.in_(("manual_pending", "manual_reviewed")), 1),
        else_=0,
    )
    rows = (
        await db.execute(
            select(
                ExamSubmission.paper_id,
                func.count(),
                func.sum(gradable_flag),
                func.sum(auto_flag),
                func.sum(manual_flag),
            )
            .join(ExamSubmission, ExamSubmission.id == ExamSubmissionItem.submission_id)
            .where(*cond)
            .group_by(ExamSubmission.paper_id)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    titles = dict(
        (
            await db.execute(
                select(ExamPaper.id, ExamPaper.title).where(
                    ExamPaper.id.in_(ids), ExamPaper.tenant_id == tenant_id
                )
            )
        ).all()
    )
    out = []
    for pid, total, gradable, auto, manual in rows:
        total = int(total or 0)
        gradable = int(gradable or 0)
        auto = int(auto or 0)
        manual = int(manual or 0)
        cov = ratio(auto, gradable)
        out.append(
            {
                "paper_id": pid,
                "title": titles.get(pid, f"试卷 {pid}"),
                "items": total,
                "gradable": gradable,
                "auto": auto,
                "manual": manual,
                "coverage": cov,
                "manual_rate": ratio(manual, total),
                "status": (
                    "unknown"
                    if cov is None
                    else ("ok" if cov >= 0.99 else ("watch" if cov >= 0.90 else "below"))
                ),
            }
        )
    return out


async def collect(
    db: AsyncSession,
    tenant_id: str,
    days: Optional[int] = DEFAULT_DAYS,
    paper_id: Optional[int] = None,
) -> dict:
    """汇总一次面板数据。``days<=0`` / ``None`` = 全量口径。"""
    cond = [ExamSubmission.tenant_id == tenant_id]
    win = _window_cond(days)
    if win is not None:
        cond.append(win)
    if paper_id:
        cond.append(ExamSubmission.paper_id == paper_id)

    items = await _item_counts(db, cond)
    sources = await _source_counts(db, cond)
    tickets = await _ticket_stats(db, tenant_id, days)
    revisions = await _revision_stats(db, tenant_id, days)
    freezes = await _freeze_stats(db, tenant_id, days)
    reviewed_auto, overturned = await _reviewed_auto(db, tenant_id, days)

    manual_total = items["by_status"].get("manual_pending", 0) + items["by_status"].get(
        "manual_reviewed", 0
    )
    frozen_manual = sources.get("manual_frozen", 0)

    metrics = {
        "coverage": coverage_metric(items["auto_gradable"], items["gradable_total"]),
        "manual_rate": manual_rate_metric(manual_total, items["total"]),
        "accuracy": accuracy_metric(
            items["by_status"].get("auto", 0), reviewed_auto, overturned
        ),
    }
    # 冻结导致的转人工单列：它是「主动停判」，不是「解析不了」。
    # 不拆出来的话，冻结一次就以为是解析退化了。
    metrics["coverage"]["frozen_manual"] = frozen_manual
    if frozen_manual and metrics["coverage"]["denominator"]:
        metrics["coverage"]["note"] = (
            "分母只含选择题与填空题（解答题本就该人判，算进去永不达标）；"
            f"其中 {frozen_manual} 题因标答冻结而主动停判"
        )

    return {
        "tenant_id": tenant_id,
        "window_days": None if not days or days <= 0 else int(days),
        "generated_at": _utcnow().isoformat(),
        "items": items,
        "metrics": metrics,
        "sources": source_rows(sources, items["total"]),
        "tickets": tickets,
        "revisions": revisions,
        "freezes": freezes,
        "by_paper": await _by_paper(db, cond, tenant_id),
    }
