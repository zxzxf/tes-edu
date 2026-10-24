"""学情数据访问层：事件流水（student_events）+ 知识点掌握度（student_mastery）。

S5 追加：student_step_errors 的取数（老师端错因分布 + /metricsz 指标）。
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import StudentEvent, StudentMastery, StudentStepError


def _parse_dt(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def _as_bool(v) -> Optional[bool]:
    """只认真正的布尔值：None（未判分）不能被当成 False。"""
    return v if isinstance(v, bool) else None


def _as_int(v) -> Optional[int]:
    try:
        return int(v)  # noqa: TC006 事件里的 step_no 可能是 str/None
    except (TypeError, ValueError):
        return None


async def record_event(db: AsyncSession, event: dict) -> StudentEvent:
    """落一条学情事件流水（含 judge v2 的步骤级归因字段）。"""
    obj = StudentEvent(
        student_id=event.get("student_id") or "",
        tenant_id=event.get("tenant_id"),
        event_type=event.get("event_type") or "unknown",
        subject=event.get("subject"),
        mode=event.get("mode"),
        question=event.get("question"),
        knowledge_points=event.get("knowledge_points") or [],
        occurred_at=_parse_dt(event.get("ts")) or datetime.now(timezone.utc),
        # ---- judge v2 扩展载荷 ----
        content_hash=event.get("content_hash") or None,
        correct=_as_bool(event.get("correct")),
        partial=_as_bool(event.get("partial")),
        first_wrong_step=_as_int(event.get("first_wrong_step")),
        error_type=(event.get("error_type") or None),
        attribution_source=(event.get("attribution_source") or None),
        evidence=(event.get("evidence") or None),
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


def extract_step_error(event: dict) -> Optional[dict]:
    """从事件里抽取步骤级错因行；不应落库时返回 None（纯函数，便于单测）。

    落库条件：有题目 hash + 有错因类型。unknown 也落 —— 它是人工复核队列的计数口径。
    """
    if not isinstance(event, dict):
        return None
    content_hash = event.get("content_hash")
    error_type = event.get("error_type")
    if not content_hash or not error_type:
        return None
    return {
        "tenant_id": event.get("tenant_id"),
        "student_id": event.get("student_id") or "",
        "content_hash": content_hash,
        "step_no": _as_int(event.get("first_wrong_step")),
        "error_type": str(error_type)[:16],
        "attribution_source": (event.get("attribution_source") or None),
        "evidence": (event.get("evidence") or None),
        "occurred_at": _parse_dt(event.get("ts")) or datetime.now(timezone.utc),
    }


async def record_step_error(db: AsyncSession, event: dict) -> bool:
    """写 student_step_errors（S5 聚合燃料）；返回是否写入。"""
    row = extract_step_error(event)
    if row is None:
        return False
    db.add(StudentStepError(**row))
    await db.commit()
    return True


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


# ---------------------------------------------------------------------------
# S5 老师端聚合取数（错因分布）
# ---------------------------------------------------------------------------

async def list_step_errors(
    db: AsyncSession,
    tenant_id: Optional[str] = None,
    student_ids: Optional[List[str]] = None,
    content_hashes: Optional[List[str]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 5000,
) -> List[StudentStepError]:
    """按范围取步骤级错因流水（老师端错因分布燃料）。

    student_step_errors 没有 subject 列，学科过滤靠调用方先筛出 content_hashes
    再传进来（学科来自 student_events）。
    """
    stmt = select(StudentStepError)
    if tenant_id:
        stmt = stmt.where(StudentStepError.tenant_id == tenant_id)
    if student_ids:
        stmt = stmt.where(StudentStepError.student_id.in_(list(student_ids)))
    if content_hashes:
        stmt = stmt.where(StudentStepError.content_hash.in_(list(content_hashes)))
    if since:
        stmt = stmt.where(StudentStepError.occurred_at >= since)
    if until:
        stmt = stmt.where(StudentStepError.occurred_at <= until)
    stmt = stmt.order_by(StudentStepError.occurred_at.desc()).limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def list_content_hashes_by_subject(
    db: AsyncSession, subject: str, since: Optional[datetime] = None
) -> List[str]:
    """某学科近期出现过的题目 hash（用于给错因流水做学科过滤）。"""
    stmt = select(StudentEvent.content_hash).where(StudentEvent.subject == subject)
    if since:
        stmt = stmt.where(StudentEvent.occurred_at >= since)
    stmt = stmt.where(StudentEvent.content_hash.isnot(None)).distinct()
    res = await db.execute(stmt)
    return [r[0] for r in res.all() if r[0]]


async def fetch_attribution_metrics(db: AsyncSession, days: int = 30) -> List[StudentStepError]:
    """取近 N 天的错因流水，供 /metricsz 输出匿名归因质量指标。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return await list_step_errors(db, since=since)
