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
from app.services import mastery as mastery_svc
from app.services import adaptive
from app.services import compose

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
        # 读取时衰减后的掌握度（T-B2 遗忘衰减；无判分记录时为 None）
        mastery_decayed = mastery_svc.compute_mastery_score(
            r.correct_count, r.wrong_count, r.last_practiced_at
        )
        items.append(
            {
                "knowledge_point": r.knowledge_point,
                "subject": r.subject,
                "practice_count": r.practice_count,
                "correct_count": r.correct_count,
                "wrong_count": r.wrong_count,
                "mastery": mastery,
                "mastery_decayed": mastery_decayed,
                "last_practiced_at": r.last_practiced_at,
            }
        )
    return {"student_id": student_id, "items": items}


@router.get("/recommendations", summary="自适应推题（ZPD + 难度自适应 + 间隔重复）")
async def get_recommendations(
    student_id: str = Query(...),
    n: int = Query(5, ge=1, le=20),
    subject: Optional[str] = None,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """T-B3：只推 ZPD 内（前置就绪、自身未掌握）的题；难度随掌握度自适应；
    附带间隔重复复习计划。学生查自己、老师/家长查其学生。"""
    return await adaptive.recommend(db, student_id, n=n, subject=subject)


@router.get("/variants", summary="变式题（给定知识点，返回该点及下游相关题）")
async def get_variants(
    kp: str = Query(..., description="知识点 code，如 MA-B4-C2-S1-K03"),
    n: int = Query(5, ge=1, le=50),
    difficulty_lo: Optional[int] = Query(None, ge=1, le=5),
    difficulty_hi: Optional[int] = Query(None, ge=1, le=5),
    types: Optional[str] = Query(None, description="题型过滤，逗号分隔（单选/multi/解答…）"),
    exclude_hashes: Optional[str] = Query(None, description="排除已出题 content_hash，逗号分隔"),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """T-A4：同点变式（tier0）优先，直接/间接下游题兜底；每题带 kp_hit/tier 可解释。"""
    type_list = [t for t in (types or "").split(",") if t.strip()]
    excl = [h for h in (exclude_hashes or "").split(",") if h.strip()]
    return await compose.compose_variants(
        db, kp, n=n,
        difficulty_lo=difficulty_lo, difficulty_hi=difficulty_hi,
        types=type_list or None, exclude_hashes=excl or None,
    )


@router.get("/compose", summary="组卷（按多个知识点 + 难度 + 题型拉题）")
async def get_compose(
    kps: str = Query(..., description="知识点 code 列表，逗号分隔"),
    n: int = Query(10, ge=1, le=100),
    difficulty_lo: Optional[int] = Query(None, ge=1, le=5),
    difficulty_hi: Optional[int] = Query(None, ge=1, le=5),
    types: Optional[str] = Query(None, description="题型过滤，逗号分隔（单选/multi/解答…）"),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT, Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """T-A4：多知识点均分配额组卷，hash 去重，不足 n 时如实返回现有题量。"""
    kp_list = [k for k in (kps or "").split(",") if k.strip()]
    type_list = [t for t in (types or "").split(",") if t.strip()]
    return await compose.compose_exam(
        db, kp_list, n=n,
        difficulty_lo=difficulty_lo, difficulty_hi=difficulty_hi,
        types=type_list or None,
    )


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
