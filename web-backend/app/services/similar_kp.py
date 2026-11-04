"""M3-5 触类旁通：同知识点 + **同条件**的变式推荐。

为什么不能只按知识点召回
------------------------
实测（2026-10-29）：学生问的等比数列题与题库里另一版本只差一个「各项均为正数」，
相似度 0.9352，但标答从 ±2 变成 2。直接推这题，学生按本题条件算出的正确答案
会被判错——这是最伤信任的一类错误。

所以这里复用 agent 侧 ``judge_v2.extra_conditions`` 的同一套条件词表与切分规则
（逻辑复制而不是跨服务调用：agent-backend 与本服务独立部署，导包会变成硬依赖），
候选一旦比本题多出限定条件，**直接剔除**，不做「提醒一下」。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_kb import CourseQuestion
from app.models.ops import QuestionKp

#: 与 agent-backend/app/core/judge_v2.py 保持逐字一致（改一处必须同步改另一处，
#: 并有单测锁「两边词表相同」）
CONDITION_WORDS: tuple = (
    "正数", "正整数", "负数", "负整数", "非负", "正实数", "不为零", "非零",
    "不等于0", "不等于 0", "递增", "递减", "单调", "互不相同", "互异",
    "整数", "实数", "有理数", "自然数", "锐角", "钝角", "直角",
    "第一象限", "第二象限", "第三象限", "第四象限", "至少", "至多", "恒成立",
)
COND_CLAUSE_SPLIT = re.compile(r"[，,。;；、]")


def _cond_norm(s: str) -> str:
    return re.sub(r"[\s，,。.;；:：、()（）\[\]{}\$]+", "", (s or "").strip().lower())


def extra_conditions(stem: str, question: str) -> List[str]:
    """题库题干里学生题面**完全没有**的限定性条件子句。"""
    qn = _cond_norm(question)
    out: List[str] = []
    for clause in COND_CLAUSE_SPLIT.split(stem or ""):
        c = _cond_norm(clause)
        if not c or c in qn:
            continue
        if any(w in c for w in CONDITION_WORDS):
            out.append(clause.strip())
    return out


def _kps_of(row: Any) -> List[str]:
    return [str(k) for k in (getattr(row, "knowledge_points", None) or []) if k]


async def bound_kp_codes(db: AsyncSession, question: Any) -> List[str]:
    """本题的 DAG code 集合：优先 ``question_kp`` 绑定，其次题库标签里的 code。"""
    tags = [k for k in _kps_of(question) if k]
    qid = getattr(question, "question_id", None) or getattr(question, "id", None)
    codes = []
    if qid:
        rows = (
            await db.execute(select(QuestionKp.kp_code).where(QuestionKp.question_id == qid))
        ).all()
        codes = [r[0] for r in rows if r[0]]
    return sorted(set(codes) | set(tags))


async def similar_same_conditions(
    db: AsyncSession,
    *,
    tenant_id: str,
    paper_question: Any,
    n: int = 3,
) -> Dict[str, Any]:
    """召回同知识点候选，剔除「本题面没有的限定条件」。

    返回 ``{"kp_codes", "items", "excluded", "total"}``；``excluded`` 里带上被剔除
    的原因，教师端能看到「不是没题，是题的条件不一样」。
    """
    stem = getattr(paper_question, "stem", "") or ""
    kps = await bound_kp_codes(db, paper_question)
    if not kps:
        return {
            "kp_codes": [], "items": [], "excluded": [], "total": 0,
            "note": "本题尚未绑定知识点，先在「题↔知识点」里标注后才能推题",
        }

    rows = (
        await db.execute(
            select(CourseQuestion).where(
                CourseQuestion.tenant_id == tenant_id,
                CourseQuestion.status == "published",
                (CourseQuestion.source_tag.is_(None)) | (CourseQuestion.source_tag != "quarantine"),
            ).order_by(CourseQuestion.id.desc()).limit(300)
        )
    ).scalars().all()

    same_hash = getattr(paper_question, "content_hash", None)
    items, excluded = [], []
    for row in rows:
        if same_hash and row.content_hash == same_hash:
            continue
        hit = sorted(set(kps) & set(_kps_of(row)))
        if not hit:
            continue
        extra = extra_conditions(row.stem or "", stem)
        if extra:
            excluded.append({
                "question_id": row.id,
                "content_hash": row.content_hash,
                "reason": f"题库题多出限定条件：{'；'.join(extra)}",
            })
            continue
        items.append({
            "question_id": row.id,
            "content_hash": row.content_hash,
            "stem": row.stem,
            "type": row.type,
            "difficulty": row.difficulty,
            "kp_hit": hit,
            "answer": row.answer,
        })
        if len(items) >= n:
            break
    return {
        "kp_codes": kps,
        "items": items,
        "excluded": excluded[:20],
        "total": len(items),
        "note": "" if items else "同知识点下没有条件一致的变式题",
    }