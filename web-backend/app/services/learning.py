"""学情数据访问层：事件流水（student_events）+ 知识点掌握度（student_mastery）。

S5 追加：student_step_errors 的取数（老师端错因分布 + /metricsz 指标）。
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_kb import CourseQuestion
from app.models.ops import (
    StudentEvent,
    StudentMastery,
    StudentStepError,
    WrongQuestion,
)


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
        # ---- T-C1/T-C3/T-C4 引导式对话埋点载荷（hint_shown/direct_reveal/solve）----
        intent=event.get("intent") or None,
        intent_source=event.get("intent_source") or None,
        hint_level=_as_int(event.get("hint_level")),
        hint_level_name=event.get("hint_level_name") or None,
        hint_rounds=_as_int(event.get("hint_rounds")),
        hint_max_rounds=_as_int(event.get("hint_max_rounds")),
        hint_step=_as_int(event.get("hint_step")),
        hint_step_total=_as_int(event.get("hint_step_total")),
        hint_frustration_streak=_as_int(event.get("hint_frustration_streak")),
        hint_finale=_as_bool(event.get("hint_finale")),
        hint_direct_reveal=_as_bool(event.get("hint_direct_reveal")),
        question_tier=event.get("question_tier") or None,
        question_tier_name=event.get("question_tier_name") or None,
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


# ---------------------------------------------------------------------------
# T2 错题本自动写入链路
# ---------------------------------------------------------------------------

def extract_wrong_question(event: dict) -> Optional[dict]:
    """从判分事件抽取错题本行；不应入库时返回 None（纯函数，便于单测）。

    入库条件：判分为错（correct is False；未判分的 None 不算错）且不是过程缺失
    （need_process 为 True 时 correct 本就为 None，这里双保险，防异常路径/旧数据）。
    不要求 content_hash / error_type —— 错题本第一职责是记错题，归因是增强信息：
    hash 缺失时 correction 为 null，读取端已容忍（见 ops.enrich_wrong）。
    """
    if not isinstance(event, dict):
        return None
    if event.get("correct") is not False:
        return None
    if event.get("need_process") is True:
        return None
    kps = event.get("knowledge_points") or []
    kp_first = kps[0][:128] if kps and isinstance(kps[0], str) else None
    return {
        "student_id": event.get("student_id") or "",
        # question 允许先缺（NOT NULL 列在 DB 层用 course_kb.stem 或占位文案兜底）
        "question": (event.get("question") or "").strip() or None,
        # 学生错答：优先原始作答，没有则用归因证据片段
        "wrong_answer": (event.get("student_answer") or event.get("evidence") or None),
        "knowledge_point": kp_first,
        "content_hash": event.get("content_hash") or None,
        "wrong_step_no": _as_int(event.get("first_wrong_step")),
        "error_type": (str(event["error_type"])[:16] if event.get("error_type") else None),
        "evidence": event.get("evidence") or None,
        "attribution_source": (event.get("attribution_source") or None),
    }


async def _find_wrong(
    db: AsyncSession, student_id: str, content_hash: Optional[str], wrong_step_no: Optional[int]
) -> Optional[WrongQuestion]:
    """按 (student_id, content_hash, wrong_step_no) 找已有错题（NULL 用 IS NULL 语义）。"""
    cond = [WrongQuestion.student_id == student_id]
    if content_hash:
        cond.append(WrongQuestion.content_hash == content_hash)
    else:
        cond.append(WrongQuestion.content_hash.is_(None))
    if wrong_step_no is not None:
        cond.append(WrongQuestion.wrong_step_no == wrong_step_no)
    else:
        cond.append(WrongQuestion.wrong_step_no.is_(None))
    res = await db.execute(select(WrongQuestion).where(*cond).limit(1))
    return res.scalars().first()


async def _fetch_course_kb(db: AsyncSession, content_hash: str) -> Optional[CourseQuestion]:
    res = await db.execute(
        select(CourseQuestion).where(CourseQuestion.content_hash == content_hash).limit(1)
    )
    return res.scalars().first()


async def record_wrong_question(db: AsyncSession, event: dict) -> bool:
    """判分出错时自动落错题本（T2）。返回是否**新插入**（已存在则只刷新，不重复插）。

    幂等：按 (student_id, content_hash, wrong_step_no) 去重，Kafka 重复消费不涨数；
    题干/知识点回填：事件缺 question 或 knowledge_point 时按 content_hash 取 course_kb。
    """
    row = extract_wrong_question(event)
    if row is None:
        return False
    now = datetime.now(timezone.utc)

    existing = await _find_wrong(db, row["student_id"], row["content_hash"], row["wrong_step_no"])
    if existing is not None:
        # 重复消费：刷新时间与归因（后续判分可能更完整，如 llm 补判），不插新行
        existing.created_at = now
        if row["error_type"]:
            existing.error_type = row["error_type"]
        if row["evidence"]:
            existing.evidence = row["evidence"]
        await db.commit()
        return False

    if row["content_hash"] and (not row["question"] or not row["knowledge_point"]):
        kb = await _fetch_course_kb(db, row["content_hash"])
        if kb is not None:
            if not row["question"]:
                row["question"] = kb.stem
            if not row["knowledge_point"]:
                kps = kb.knowledge_points or []
                kp = kps[0] if kps and isinstance(kps[0], str) else None
                row["knowledge_point"] = kp[:128] if kp else None
    if not row["question"]:
        # question 列 NOT NULL：占位文案保证可追溯，hash 本身也在 content_hash 列里
        row["question"] = f"（题干待补全：content_hash={row['content_hash'] or '未知'}）"

    db.add(
        WrongQuestion(
            student_id=row["student_id"],
            question=row["question"],
            wrong_answer=row["wrong_answer"],
            knowledge_point=row["knowledge_point"],
            content_hash=row["content_hash"],
            wrong_step_no=row["wrong_step_no"],
            error_type=row["error_type"],
            evidence=row["evidence"],
            attribution_source=row["attribution_source"],
            created_at=now,
        )
    )
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
