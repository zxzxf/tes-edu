"""M3-3 题↔知识点绑定 + 覆盖度统计。

红线：``kp_code`` 写侧唯一入口是 ``mastery.resolve_kp_codes``——它负责把中文名称
翻译成 DAG code，翻不到的键返回 None。这里**只接受已解析的 code**，不允许直接
写名称，否则又会造出第二套键（图谱按 code 查永远为空）。

绑定有两种来源：
  * 显式绑定：教师端批量标注 / 导入时带 ``kp_codes``；
  * 隐式回填：题目 ``course_kb.knowledge_points`` 里已是 code 的，绑定接口可一键补齐。

覆盖度统计按 kp 聚合题量，并把「一题都没绑」的题列进待标注队列——
没有这道队列，覆盖率看板就只是个数字，教师不知道去补哪道题。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_kb import CourseQuestion
from app.models.ops import QuestionKp
from app.services import mastery as mastery_svc


async def bind_kp_codes(
    db: AsyncSession,
    *,
    tenant_id: str,
    question_ids: Sequence[int],
    kp_codes: Sequence[str],
    source: str = "manual",
    reviewed_by: Optional[str] = None,
) -> Dict[str, Any]:
    """把若干题绑定到若干知识点（幂等）。

    返回 ``{"bound": n, "created": n, "skipped": [...], "unresolved": [...]}``：
    - ``skipped``：同一 (question_id, kp_code) 已存在的行（重复绑定不算错）；
    - ``unresolved``：传进来的键在 DAG 里不存在，**不写库**并原样回报。
    """
    keys = [str(k) for k in (kp_codes or []) if str(k).strip()]
    ids = [int(i) for i in (question_ids or [])]
    out = {"bound": 0, "created": 0, "skipped": [], "unresolved": []}
    if not keys or not ids:
        out["unresolved"] = keys
        return out

    resolved = await mastery_svc.resolve_kp_codes(db, keys)
    codes = []
    for key in keys:
        code = resolved.get(key)
        if code:
            codes.append(code)
        else:
            out["unresolved"].append(key)
    codes = sorted(set(codes))
    if not codes:
        return out

    rows = (
        await db.execute(
            select(CourseQuestion).where(
                CourseQuestion.id.in_(ids),
                CourseQuestion.tenant_id == tenant_id,
            )
        )
    ).scalars().all()
    by_id = {r.id: r for r in rows}

    existing = (
        await db.execute(
            select(QuestionKp).where(
                QuestionKp.question_id.in_(list(by_id)),
                QuestionKp.kp_code.in_(codes),
            )
        )
    ).scalars().all()
    seen = {(r.question_id, r.kp_code) for r in existing}

    for qid in list(by_id):
        for code in codes:
            if (qid, code) in seen:
                out["skipped"].append({"question_id": qid, "kp_code": code})
                continue
            db.add(
                QuestionKp(
                    tenant_id=tenant_id,
                    question_id=qid,
                    content_hash=by_id[qid].content_hash,
                    kp_code=code,
                    weight=1,
                    source=source,
                    reviewed_by=reviewed_by,
                )
            )
            out["created"] += 1
            out["bound"] += 1
    await db.flush()
    return out


async def bind_from_course_kb(
    db: AsyncSession,
    *,
    tenant_id: str,
    question_ids: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """把题库行的 ``knowledge_points`` 里**已经是 code** 的标签回填到 ``question_kp``。

    只回填能解析的：名称（如「等差数列的通项公式」）需要 ``resolve_kp_codes``
    翻译，翻译不到就跳过并计入 ``unresolved``，不写脏键。
    """
    stmt = select(CourseQuestion).where(CourseQuestion.tenant_id == tenant_id)
    if question_ids:
        stmt = stmt.where(CourseQuestion.id.in_([int(i) for i in question_ids]))
    rows = (await db.execute(stmt)).scalars().all()

    keys = sorted({str(k) for r in rows for k in (r.knowledge_points or []) if k})
    resolved = await mastery_svc.resolve_kp_codes(db, keys) if keys else {}
    unresolved = [k for k in keys if not resolved.get(k)]

    existing = {
        (r.question_id, r.kp_code)
        for r in (
            await db.execute(select(QuestionKp).where(QuestionKp.tenant_id == tenant_id))
        ).scalars().all()
    }

    created = 0
    for row in rows:
        for kp in row.knowledge_points or []:
            code = resolved.get(str(kp))
            if not code or (row.id, code) in existing:
                continue
            db.add(
                QuestionKp(
                    tenant_id=tenant_id,
                    question_id=row.id,
                    content_hash=row.content_hash,
                    kp_code=code,
                    weight=1,
                    source="import",
                )
            )
            existing.add((row.id, code))
            created += 1
    await db.flush()
    return {
        "scanned": len(rows),
        "created": created,
        "unresolved": unresolved,
    }


async def coverage(db: AsyncSession, tenant_id: str) -> Dict[str, Any]:
    """覆盖度：按 kp 聚合题量 + 未绑定题队列。

    只统计本租户 ``course_kb`` 行；``question_kp`` 里指向已删除题的行不计入
    （按 question_id 回连）。
    """
    total = (
        await db.execute(
            select(func.count()).select_from(CourseQuestion).where(
                CourseQuestion.tenant_id == tenant_id
            )
        )
    ).scalar() or 0

    rows = (
        await db.execute(
            select(QuestionKp.kp_code, func.count(func.distinct(QuestionKp.question_id)))
            .join(CourseQuestion, CourseQuestion.id == QuestionKp.question_id)
            .where(CourseQuestion.tenant_id == tenant_id)
            .group_by(QuestionKp.kp_code)
            .order_by(func.count(func.distinct(QuestionKp.question_id)).desc())
        )
    ).all()
    by_kp = [{"kp_code": code, "question_count": int(n)} for code, n in rows if code]

    bound_ids = {
        r[0]
        for r in (
            await db.execute(
                select(func.distinct(QuestionKp.question_id))
                .join(CourseQuestion, CourseQuestion.id == QuestionKp.question_id)
                .where(CourseQuestion.tenant_id == tenant_id)
            )
        ).all()
    }
    unbound_stmt = select(
        CourseQuestion.id, CourseQuestion.stem, CourseQuestion.content_hash
    ).where(CourseQuestion.tenant_id == tenant_id)
    if bound_ids:
        unbound_stmt = unbound_stmt.where(CourseQuestion.id.notin_(list(bound_ids)))
    unbound = (
        await db.execute(unbound_stmt.order_by(CourseQuestion.id.desc()).limit(200))
    ).all()

    return {
        "total_questions": int(total),
        "bound_questions": len(bound_ids),
        "unbound_count": int(total) - len(bound_ids),
        "coverage_rate": round(len(bound_ids) / total, 4) if total else 0.0,
        "by_kp": by_kp,
        "unbound_examples": [
            {"question_id": int(r[0]), "stem": r[1], "content_hash": r[2]}
            for r in unbound
        ],
    }