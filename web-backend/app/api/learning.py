"""学情查询接口：知识点掌握度 + 事件流水。

数据来源为 agent-backend 上报的 student_event（经 Kafka 消费落库）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.services import learning

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/mastery", summary="学生知识点掌握度")
async def get_mastery(
    student_id: str = Query(...),
    subject: Optional[str] = None,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    rows = await learning.list_mastery(db, student_id, subject)
    items = []
    for r in rows:
        judged = (r.correct_count or 0) + (r.wrong_count or 0)
        # 未接入判分前没有对错计数，mastery 为 None（只体现练习量）
        mastery = round((r.correct_count or 0) / judged, 4) if judged else None
        items.append(
            {
                "knowledge_point": r.knowledge_point,
                "subject": r.subject,
                "practice_count": r.practice_count,
                "correct_count": r.correct_count,
                "wrong_count": r.wrong_count,
                "mastery": mastery,
                "last_practiced_at": r.last_practiced_at,
            }
        )
    return {"student_id": student_id, "items": items}


@router.get("/events", summary="学生近期学情事件")
async def get_events(
    student_id: str = Query(...),
    limit: int = Query(50, le=200),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    rows = await learning.list_events(db, student_id, limit)
    items = [
        {
            "id": r.id,
            "event_type": r.event_type,
            "subject": r.subject,
            "mode": r.mode,
            "question": (r.question or "")[:120],
            "knowledge_points": r.knowledge_points,
            "occurred_at": r.occurred_at,
        }
        for r in rows
    ]
    return {"student_id": student_id, "items": items}
