"""B 端运营实体的真实数据访问层（替换原 API 层的内存 _STORE 占位）。

S5 追加：错题本 enrich —— 按 (content_hash, wrong_step_no) 取步骤的 correction，
并取知识点卡的 understanding/memory，让错题本从"记一道错题"升级为"知道错在第几步、怎么改"。
"""
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import (
    ComplianceItem,
    CourseKbStep,
    KnowledgePoint,
    Submission,
    WrongQuestion,
)
from app.services import attribution


# ---------------- 知识点 ----------------
async def list_knowledge(
    db: AsyncSession,
    subject: Optional[str] = None,
    grade: Optional[str] = None,
    limit: int = 100,
) -> List[KnowledgePoint]:
    stmt = select(KnowledgePoint)
    if subject:
        stmt = stmt.where(KnowledgePoint.subject == subject)
    if grade:
        stmt = stmt.where(KnowledgePoint.grade == grade)
    res = await db.execute(stmt.limit(limit))
    return list(res.scalars().all())


async def create_knowledge(db: AsyncSession, payload: dict) -> KnowledgePoint:
    obj = KnowledgePoint(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ---------------- 错题本 ----------------
async def list_wrong(
    db: AsyncSession, student_id: str, limit: int = 100
) -> List[WrongQuestion]:
    stmt = select(WrongQuestion).where(WrongQuestion.student_id == student_id)
    res = await db.execute(stmt.limit(limit))
    return list(res.scalars().all())


async def create_wrong(db: AsyncSession, payload: dict) -> WrongQuestion:
    obj = WrongQuestion(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ---------------- 错题本 enrich（S5 ④）----------------
async def fetch_steps(
    db: AsyncSession, pairs: Sequence[Tuple[str, int]]
) -> Dict[Tuple[str, int], CourseKbStep]:
    """按 (content_hash, step_no) 批量取步骤（含归因映射，供 correction 抽取）。"""
    pairs = [p for p in (pairs or []) if p and p[0]]
    if not pairs:
        return {}
    stmt = select(CourseKbStep).where(
        tuple_(CourseKbStep.content_hash, CourseKbStep.step_no).in_(pairs)
    )
    res = await db.execute(stmt)
    return {(s.content_hash, s.step_no): s for s in res.scalars().all()}


async def fetch_kp_cards(db: AsyncSession, names: Sequence[str]) -> Dict[str, KnowledgePoint]:
    """按知识点名取知识点卡（understanding/memory）。"""
    names = [n for n in (names or []) if n]
    if not names:
        return {}
    res = await db.execute(
        select(KnowledgePoint).where(KnowledgePoint.title.in_(names))
    )
    return {k.title: k for k in res.scalars().all() if k.title}


async def enrich_wrong(db: AsyncSession, rows: Sequence[WrongQuestion]) -> List[dict]:
    """错题本列表增强：原字段 + 错因中文名 + 该步正确做法 + 知识点卡。

    取不到步骤（历史数据无 content_hash）时原字段照常返回，增强字段为 None，
    不因为"查不到"就让整个列表报错。
    """
    rows = list(rows or [])
    steps: Dict[Tuple[str, int], CourseKbStep] = {}
    cards: Dict[str, KnowledgePoint] = {}
    if rows:
        pairs = {(w.content_hash, w.wrong_step_no or 0) for w in rows if w.content_hash}
        try:
            steps = await fetch_steps(db, pairs)
        except Exception:  # noqa: BLE001 步骤只是增强信息
            steps = {}
        if steps:
            try:
                cards = await fetch_kp_cards(
                    db, [s.kp_name for s in steps.values() if s.kp_name]
                )
            except Exception:  # noqa: BLE001
                cards = {}

    out: List[dict] = []
    for w in rows:
        item = {
            "id": w.id,
            "student_id": w.student_id,
            "question": w.question,
            "wrong_answer": w.wrong_answer,
            "correct_answer": w.correct_answer,
            "knowledge_point": w.knowledge_point,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "content_hash": w.content_hash,
            "wrong_step_no": w.wrong_step_no,
            "error_type": w.error_type,
            "evidence": w.evidence,
            "attribution_source": w.attribution_source,
        }
        step = steps.get((w.content_hash, w.wrong_step_no or 0))
        card = cards.get(step.kp_name) if (step is not None and step.kp_name) else None
        item.update(attribution.build_wrong_question_enrich(step, w.error_type, card))
        out.append(item)
    return out


# ---------------- 批改 ----------------
async def create_submission(db: AsyncSession, payload: dict) -> Submission:
    obj = Submission(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def get_submission(db: AsyncSession, submission_id: int) -> Optional[Submission]:
    return await db.get(Submission, submission_id)


async def review_submission(
    db: AsyncSession, obj: Submission, score: float, comment: str
) -> Submission:
    obj.score = score
    obj.comment = comment
    await db.commit()
    await db.refresh(obj)
    return obj


# ---------------- 合规审核 ----------------
async def list_compliance(
    db: AsyncSession, status: Optional[str] = None, limit: int = 100
) -> List[ComplianceItem]:
    stmt = select(ComplianceItem)
    if status:
        stmt = stmt.where(ComplianceItem.status == status)
    res = await db.execute(stmt.limit(limit))
    return list(res.scalars().all())


async def create_compliance(db: AsyncSession, payload: dict) -> ComplianceItem:
    obj = ComplianceItem(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def get_compliance(db: AsyncSession, item_id: int) -> Optional[ComplianceItem]:
    return await db.get(ComplianceItem, item_id)


async def review_compliance(
    db: AsyncSession, obj: ComplianceItem, status: str
) -> ComplianceItem:
    obj.status = status
    await db.commit()
    await db.refresh(obj)
    return obj
