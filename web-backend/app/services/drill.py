"""背诵卡：SM-2 间隔复习排程。

与 App 端 ``Sm2Scheduler`` 是**同一套口径**（rating<3 归零 / ease 下限 1.3 /
间隔 1→6→×ease），两侧各自有单测锁死。客户端离线也会本地改排程并排队补传，
但服务端在收到复习请求时**按服务端权威值重算**，客户端算错不会污染库里排程。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import DrillCard, DrillReview

EASE_START = 2.5
EASE_MIN = 1.3
INTERVAL_MAX = 365.0
DUE_WINDOW = 30          # 默认拉多少张到期卡


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Schedule:
    """一次复习后的排程结果。"""

    ease: float
    interval_days: float
    reps: int
    lapses: int
    due_at: datetime


def sm2_schedule(
    ease: float,
    interval_days: float,
    reps: int,
    lapses: int,
    rating: int,
    now: Optional[datetime] = None,
) -> Schedule:
    """SM-2 简版（纯函数，便于单测）。

    - rating < 3（忘了）：lapses+1、间隔归 1 天、ease-0.2（越忘越勤快地来）
    - rating >= 3：ease 微调 ``0.1-(5-rating)*(0.08+(5-rating)*0.02)``，reps+1，
      间隔 = 首次 1 天 / 第二次 6 天 / 之后 上次间隔×ease
    - ease 下限 1.3（低于这个值复习会越来越密，学生会被拖垮），间隔上限 365 天
    """
    now = now or _utcnow()
    ease = float(ease or EASE_START)
    interval_days = float(interval_days or 0.0)
    reps = int(reps or 0)
    lapses = int(lapses or 0)

    if rating < 3:
        lapses += 1
        ease = max(EASE_MIN, round(ease - 0.2, 4))
        interval_days = 1.0
        reps = 0
    else:
        delta = 0.1 - (5 - rating) * (0.08 + (5 - rating) * 0.02)
        ease = max(EASE_MIN, round(ease + delta, 4))
        reps += 1
        if reps <= 1:
            interval_days = 1.0
        elif reps == 2:
            interval_days = 6.0
        else:
            interval_days = min(INTERVAL_MAX, round(interval_days * ease, 4))

    interval_days = max(0.0, min(INTERVAL_MAX, interval_days))
    return Schedule(
        ease=ease,
        interval_days=interval_days,
        reps=reps,
        lapses=lapses,
        due_at=now + timedelta(days=interval_days),
    )


def _ms(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.timestamp() * 1000) if dt else None


def card_out(c: DrillCard) -> dict:
    """卡片 → 客户端契约（时间统一 epoch 毫秒）。"""
    return {
        "id": c.id,
        "client_id": c.client_id,
        "kp": c.kp,
        "front": c.front,
        "back": c.back,
        "due_at": _ms(c.due_at) or 0,
        "ease": float(c.ease or EASE_START),
        "interval_days": float(c.interval_days or 0.0),
        "reps": int(c.reps or 0),
        "lapses": int(c.lapses or 0),
        "deleted_at": _ms(c.deleted_at),
    }


async def list_cards(
    db: AsyncSession,
    student_id: str,
    due_only: bool = True,
    limit: int = DUE_WINDOW,
    now: Optional[datetime] = None,
) -> Tuple[List[dict], int, int]:
    """返回 (卡片列表, 到期数量, 总数)。due_only 时按到期时间升序取前 limit 张。"""
    now = now or _utcnow()
    limit = max(1, min(int(limit or DUE_WINDOW), 100))

    total = int(
        (await db.execute(
            select(func.count(DrillCard.id)).where(
                DrillCard.student_id == student_id, DrillCard.deleted_at.is_(None)
            )
        )).scalar_one()
    )
    due_count = int(
        (await db.execute(
            select(func.count(DrillCard.id)).where(
                DrillCard.student_id == student_id,
                DrillCard.deleted_at.is_(None),
                (DrillCard.due_at.is_(None)) | (DrillCard.due_at <= now),
            )
        )).scalar_one()
    )

    if due_only:
        stmt = (
            select(DrillCard)
            .where(
                DrillCard.student_id == student_id,
                DrillCard.deleted_at.is_(None),
                (DrillCard.due_at.is_(None)) | (DrillCard.due_at <= now),
            )
            .order_by(DrillCard.due_at.asc().nullsfirst())
            .limit(limit)
        )
    else:
        stmt = (
            select(DrillCard)
            .where(DrillCard.student_id == student_id, DrillCard.deleted_at.is_(None))
            .order_by(DrillCard.id.asc())
            .limit(limit)
        )
    rows = (await db.execute(stmt)).scalars().all()
    return [card_out(c) for c in rows], due_count, total


async def push_cards(
    db: AsyncSession,
    student_id: str,
    items: Sequence[dict],
    tenant_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[dict]:
    """批量 upsert：按 (student_id, client_id) 幂等，已存在则更新卡面（不动排程）。"""
    now = now or _utcnow()
    for it in items:
        cid = (it.get("client_id") or "").strip()
        front = (it.get("front") or "").strip()
        back = (it.get("back") or "").strip()
        if not front or not back:
            continue
        row = None
        if cid:
            row = (
                await db.execute(
                    select(DrillCard).where(
                        DrillCard.student_id == student_id,
                        DrillCard.client_id == cid,
                        DrillCard.deleted_at.is_(None),
                    )
                )
            ).scalars().first()
        if row is None:
            row = DrillCard(
                student_id=student_id,
                tenant_id=tenant_id,
                client_id=cid or None,
                kp=(it.get("kp") or None),
                front=front,
                back=back,
                source=(it.get("source") or "app"),
                ease=EASE_START,
                interval_days=0.0,
                due_at=now,          # 新卡当天到期，立刻能背
                reps=0,
                lapses=0,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.front = front
            row.back = back
            row.kp = it.get("kp") or row.kp
            row.updated_at = now
    await db.commit()
    rows = (
        await db.execute(
            select(DrillCard).where(
                DrillCard.student_id == student_id, DrillCard.deleted_at.is_(None)
            ).order_by(DrillCard.id.asc())
        )
    ).scalars().all()
    return [card_out(c) for c in rows]


async def review_card(
    db: AsyncSession,
    student_id: str,
    card_id: int,
    rating: int,
    rated_at: Optional[datetime] = None,
    client_id: Optional[str] = None,
) -> Optional[dict]:
    """提交一次复习评分：写流水 + 按服务端权威值重算排程。"""
    card = (
        await db.execute(
            select(DrillCard).where(
                DrillCard.id == card_id,
                DrillCard.student_id == student_id,
                DrillCard.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if card is None:
        return None

    rating = int(rating)
    if rating not in (0, 3, 4, 5):
        rating = 0 if rating < 3 else 4

    sch = sm2_schedule(card.ease, card.interval_days, card.reps, card.lapses, rating)
    card.ease = sch.ease
    card.interval_days = sch.interval_days
    card.reps = sch.reps
    card.lapses = sch.lapses
    card.due_at = sch.due_at
    card.updated_at = sch.due_at   # 占位：updated_at 由 onupdate 兜底

    db.add(
        DrillReview(
            student_id=student_id,
            card_id=card.id,
            client_id=client_id or card.client_id,
            rating=rating,
            rated_at=rated_at or datetime.now(timezone.utc),
        )
    )
    await db.commit()
    await db.refresh(card)
    return {
        "id": card.id,
        "due_at": _ms(card.due_at) or 0,
        "interval_days": float(card.interval_days or 0.0),
        "ease": float(card.ease or EASE_START),
        "reps": int(card.reps or 0),
        "lapses": int(card.lapses or 0),
    }


async def stats(
    db: AsyncSession, student_id: str, now: Optional[datetime] = None
) -> dict:
    """背诵卡概览：总数 / 到期 / 新卡 / 近 30 次复习的记住率。"""
    now = now or _utcnow()
    total = int(
        (await db.execute(
            select(func.count(DrillCard.id)).where(
                DrillCard.student_id == student_id, DrillCard.deleted_at.is_(None)
            )
        )).scalar_one()
    )
    due = int(
        (await db.execute(
            select(func.count(DrillCard.id)).where(
                DrillCard.student_id == student_id,
                DrillCard.deleted_at.is_(None),
                (DrillCard.due_at.is_(None)) | (DrillCard.due_at <= now),
            )
        )).scalar_one()
    )
    new_count = int(
        (await db.execute(
            select(func.count(DrillCard.id)).where(
                DrillCard.student_id == student_id,
                DrillCard.deleted_at.is_(None),
                DrillCard.reps == 0,
            )
        )).scalar_one()
    )

    recent = (
        await db.execute(
            select(DrillReview.rating)
            .where(DrillReview.student_id == student_id)
            .order_by(DrillReview.id.desc())
            .limit(30)
        )
    ).scalars().all()
    retention = (
        round(sum(1 for r in recent if (r or 0) >= 3) / len(recent), 4) if recent else 0.0
    )
    return {"total": total, "due": due, "new_count": new_count, "retention": retention}
