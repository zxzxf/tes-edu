"""M3-6 错题 → 知识点 → 背诵卡联动。

判错落 ``wrong_questions(source=exam)`` 时，按该题的知识点生成/推荐记忆卡；
重做正确后按掌握度上调背诵卡调度。

两条边界：
  * **同一知识点只建一张卡**（``(student_id, kp, source='wrong')``）：错 5 道同一
    知识点的题不该变成 5 张一模一样的卡，复习队列会被灌满。
  * **重做正确不改 SM-2 排程参数**，只把到期时间拉长（``ease`` 不动）：一次做对
    不等于掌握，参数乱跳会让间隔重复失去意义。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import DrillCard, WrongQuestion
from app.services import mastery as mastery_svc


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def card_front(kp_name: str) -> str:
    return f"回顾知识点：{kp_name}"


def card_back(kp_name: str, kp_row: Any = None) -> str:
    """卡背面优先用知识点卡的「记忆抓手」，没有就退回知识点名。"""
    memory = getattr(kp_row, "memory", None) if kp_row is not None else None
    if memory:
        return str(memory)
    understanding = getattr(kp_row, "understanding", None) if kp_row is not None else None
    if understanding:
        return str(understanding)[:500]
    return kp_name


async def _kp_meta(db: AsyncSession, code: str):
    from sqlalchemy import text

    row = (
        await db.execute(
            text(
                "SELECT title, memory, understanding FROM kb_knowledge_points "
                "WHERE code = :c LIMIT 1"
            ).bindparams(c=code)
        )
    ).first()
    return row


async def ensure_card_for_kp(
    db: AsyncSession,
    *,
    student_id: str,
    tenant_id: Optional[str],
    kp_code: Optional[str],
    kp_name: str,
) -> Optional[DrillCard]:
    """按知识点保证一张复习卡存在（幂等）。"""
    if not (kp_code or kp_name):
        return None
    stmt = select(DrillCard).where(
        DrillCard.student_id == student_id,
        DrillCard.deleted_at.is_(None),
        DrillCard.source == "wrong",
    )
    if kp_code:
        stmt = stmt.where(DrillCard.kp == kp_code)
    else:
        stmt = stmt.where(DrillCard.kp.is_(None), DrillCard.front == card_front(kp_name))
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        return existing

    meta = await _kp_meta(db, kp_code) if kp_code else None
    row = DrillCard(
        student_id=student_id,
        tenant_id=tenant_id,
        kp=kp_code,
        front=card_front(meta[0] if meta and meta[0] else kp_name),
        back=card_back(kp_name, meta),
        source="wrong",
        due_at=_utcnow(),
        reps=0,
        lapses=0,
    )
    db.add(row)
    await db.flush()
    return row


async def on_wrong_exam_items(
    db: AsyncSession,
    *,
    student_id: str,
    tenant_id: Optional[str],
    wrong_rows: Sequence[WrongQuestion],
) -> Dict[str, Any]:
    """判错后调用：为每道错题的 kp 建/推一张卡。

    ``wrong_rows`` 是已落库的 ``WrongQuestion``；``knowledge_point`` 里存的是
    名称或 code（历史脏键两者都有），这里统一经 ``resolve_kp_codes`` 归一。
    """
    keys = [w.knowledge_point for w in wrong_rows if w.knowledge_point]
    resolved = await mastery_svc.resolve_kp_codes(db, keys) if keys else {}
    created, reused, unresolved = 0, 0, []
    for w in wrong_rows:
        name = w.knowledge_point
        if not name:
            continue
        code = resolved.get(name)
        if code is None and name not in resolved:
            unresolved.append(name)
        before = await ensure_card_for_kp(
            db,
            student_id=student_id,
            tenant_id=tenant_id,
            kp_code=code,
            kp_name=name,
        )
        if before is None:
            unresolved.append(name)
        elif before.reps == 0 and before.lapses == 0:
            created += 1
        else:
            reused += 1
    await db.flush()
    return {"created": created, "reused": reused, "unresolved": sorted(set(unresolved))}


async def on_mastery_up(
    db: AsyncSession,
    *,
    student_id: str,
    kp_code: str,
    now: Optional[datetime] = None,
) -> Optional[DrillCard]:
    """重做正确 / 掌握度上调：把该 kp 的复习卡到期时间往后推。

    只动 ``due_at``，不动 ``ease`` / ``interval_days`` —— 见模块 docstring。
    """
    now = now or _utcnow()
    row = (
        await db.execute(
            select(DrillCard).where(
                DrillCard.student_id == student_id,
                DrillCard.kp == kp_code,
                DrillCard.source == "wrong",
                DrillCard.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if row is None:
        return None
    # 至少隔 1 天再看：刚做对就立刻再问，等于没复习
    row.due_at = now + timedelta(days=1)
    row.updated_at = now
    return row