"""学情数据访问层：事件流水（student_events）+ 知识点掌握度（student_mastery）。"""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import StudentEvent, StudentMastery


def _parse_dt(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


async def record_event(db: AsyncSession, event: dict) -> StudentEvent:
    """落一条学情事件流水。"""
    obj = StudentEvent(
        student_id=event.get("student_id") or "",
        tenant_id=event.get("tenant_id"),
        event_type=event.get("event_type") or "unknown",
        subject=event.get("subject"),
        mode=event.get("mode"),
        question=event.get("question"),
        knowledge_points=event.get("knowledge_points") or [],
        occurred_at=_parse_dt(event.get("ts")) or datetime.now(timezone.utc),
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def bump_practice(
    db: AsyncSession,
    student_id: str,
    knowledge_point: str,
    tenant_id: Optional[str] = None,
    subject: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    correct: Optional[bool] = None,
) -> None:
    """按 学生×知识点 累计练习量与对错（原子 upsert，避免并发丢更新）。

    correct=True -> correct_count+1；False -> wrong_count+1；None（未判分）-> 只计练习量。
    """
    now = occurred_at or datetime.now(timezone.utc)
    stmt = pg_insert(StudentMastery).values(
        student_id=student_id,
        tenant_id=tenant_id,
        subject=subject,
        knowledge_point=knowledge_point,
        practice_count=1,
        correct_count=1 if correct is True else 0,
        wrong_count=1 if correct is False else 0,
        last_practiced_at=now,
    )
    set_ = {
        "practice_count": StudentMastery.practice_count + 1,
        "last_practiced_at": now,
        "tenant_id": tenant_id,
        "subject": subject,
    }
    if correct is True:
        set_["correct_count"] = StudentMastery.correct_count + 1
    elif correct is False:
        set_["wrong_count"] = StudentMastery.wrong_count + 1
    stmt = stmt.on_conflict_do_update(
        index_elements=["student_id", "knowledge_point"], set_=set_
    )
    await db.execute(stmt)
    await db.commit()


async def list_mastery(
    db: AsyncSession, student_id: str, subject: Optional[str] = None
) -> List[StudentMastery]:
    stmt = select(StudentMastery).where(StudentMastery.student_id == student_id)
    if subject:
        stmt = stmt.where(StudentMastery.subject == subject)
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def list_events(
    db: AsyncSession, student_id: str, limit: int = 50
) -> List[StudentEvent]:
    stmt = (
        select(StudentEvent)
        .where(StudentEvent.student_id == student_id)
        .order_by(StudentEvent.id.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())
