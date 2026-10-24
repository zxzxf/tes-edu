"""模块2：学情看板与薄弱点诊断。

- /analytics/summary：总览（既有，占位数据，待接入真实聚合）。
- /analytics/error-distribution：**S5 老师端班级错因分布**（按错因/按题/按步骤聚合，
  支持 CSV 导出，用于教研会素材）。

班级口径说明：当前无班级/分组表，试点期间以 tenant_id 为范围，
可用 student_ids 显式圈定一批学生（逗号分隔）。
学科过滤：student_step_errors 无 subject 列，先按 student_events 取该学科的
题目 content_hash，再据此过滤错因流水。
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import CourseQuestion
from app.models.schemas import AnalyticsSummary
from app.services import attribution, learning
from app.services.reports import fetch_step_texts

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummary)
def summary(
    student_id: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
):
    # 占位：真实场景从 student_logs + pgvector 聚合薄弱点
    return AnalyticsSummary(
        student_id=student_id,
        weak_points=["二次函数", "被动语态", "几何证明", "文言文虚词"],
        mastery=0.72,
        trend=[0.55, 0.6, 0.63, 0.68, 0.72, 0.74],
    )


async def _fetch_question_titles(db: AsyncSession, hashes: List[str]) -> dict:
    """题目 hash → 题干（老师端要看到"哪道题"，不能只给 hash）。"""
    hashes = [h for h in (hashes or []) if h]
    if not hashes:
        return {}
    res = await db.execute(
        select(CourseQuestion.content_hash, CourseQuestion.stem).where(
            CourseQuestion.content_hash.in_(hashes)
        )
    )
    return {h: (s or "") for h, s in res.all()}


@router.get("/error-distribution")
async def error_distribution(
    tenant_id: Optional[str] = Query(None, description="租户/班级范围"),
    student_ids: Optional[str] = Query(None, description="逗号分隔的学生ID，空则取租户全部"),
    subject: Optional[str] = Query(None, description="学科，如 数学"),
    period_days: int = Query(30, ge=1, le=180),
    group_by: str = Query("error_type", pattern="^(error_type|question|step)$"),
    top_n: int = Query(20, ge=1, le=200),
    format: str = Query("json", pattern="^(json|csv)$"),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    """班级错因分布（S5 ③）。

    group_by=error_type —— 七类错因分布（教研会最常用的口径）
    group_by=question   —— 按题聚合（哪道题错得最多）
    group_by=step       —— 按 (题, 步骤) 聚合（精确到"卡在第几步"）
    format=csv          —— 导出 CSV（Excel 可直接打开）
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=period_days)
    ids = [s.strip() for s in (student_ids or "").split(",") if s.strip()] or None

    hashes: Optional[List[str]] = None
    if subject:
        # 学科过滤：先取该学科近期出现过的题目，再过滤错因流水
        hashes = await learning.list_content_hashes_by_subject(
            db, subject, since=now - timedelta(days=max(period_days, 90))
        )
        if not hashes:
            rows: List = []
            dist = attribution.build_class_distribution(rows, group_by, top_n=top_n)
            return _wrap(dist, tenant_id, subject, period_days, since, now, rows, 0)

    rows = await learning.list_step_errors(
        db,
        tenant_id=tenant_id,
        student_ids=ids,
        content_hashes=hashes,
        since=since,
        until=now,
    )

    # 展示增强：步骤文本 + 题干（取不到不影响聚合数字）
    step_texts, titles = {}, {}
    if rows:
        try:
            step_texts = await fetch_step_texts(
                db, {(r.content_hash, r.step_no or 0) for r in rows if r.content_hash}
            )
        except Exception:  # noqa: BLE001
            step_texts = {}
        try:
            titles = await _fetch_question_titles(
                db, list({r.content_hash for r in rows if r.content_hash})
            )
        except Exception:  # noqa: BLE001
            titles = {}

    dist = attribution.build_class_distribution(
        rows, group_by, step_texts=step_texts, question_titles=titles, top_n=top_n
    )

    if format == "csv":
        csv_text = attribution.distribution_csv(dist)
        fname = quote(f"错因分布_按{group_by}_{now.date()}.csv")
        return Response(
            content="\ufeff" + csv_text,  # BOM：Excel 打开中文不乱码
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
        )

    student_count = len({r.student_id for r in rows if r.student_id})
    return _wrap(dist, tenant_id, subject, period_days, since, now, rows, student_count)


def _wrap(dist: dict, tenant_id, subject, period_days, since, now, rows, student_count) -> dict:
    """统一响应外壳：范围说明 + 分布 + 归因来源占比（老师端可对账）。"""
    return {
        "scope": {
            "tenant_id": tenant_id,
            "subject": subject,
            "period_days": period_days,
            "from": since.isoformat(),
            "to": now.isoformat(),
            "student_count": student_count,
        },
        "total": dist["total"],
        "group_by": dist["group_by"],
        "rows": dist["rows"],
        "attribution": attribution.attribution_source_ratio(rows),
    }
