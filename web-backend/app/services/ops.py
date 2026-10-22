"""B 端运营实体的真实数据访问层（替换原 API 层的内存 _STORE 占位）。"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import ComplianceItem, KnowledgePoint, Submission, WrongQuestion


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
