"""M5 课程采集与知识体系接口（课程源 / 采集任务 / 解析 pipeline / 知识点审核）。

教师端挂在 ``/api/admin/admin``? —— 不：本 router 前缀 ``/course``，
挂载后路径为 ``/api/admin/course/...``，与既有 ``/api/admin/questions`` 同级。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import read_tenant_id, require_role
from app.models.ops import CrawlJob, CrawlRun, CourseSource, KpNode
from app.crawl_service import scheduler as crawl_svc
from app.crawl_service import intake as crawl_intake
from app.crawl_service import discovery as crawl_disc
from app.services import course_pipeline

router = APIRouter(prefix="/course", tags=["course"])


def _tenant(user: dict) -> str:
    """写侧租户：新建/审核/启停/删除/回退一律严格按本租户隔离。"""
    return user.get("tenant_id") or "school_pilot_01"


def _read_tenant(user: dict) -> Optional[str]:
    """读侧租户：平台级角色返回 None（跨租户只读），其余按本租户。"""
    return read_tenant_id(user)


# ------------------------------------------------------------------ 课程源
class SourceCreate(BaseModel):
    name: str
    source_type: str = "file"          # file/url/bank_pack
    uri: str
    parser: str = "auto"


class SourceOut(BaseModel):
    id: int
    name: str
    source_type: str
    uri: str
    parser: str
    status: str
    ingested_at: Optional[str] = None
    #: M5-7 自主发现标记与审核状态
    auto_discovered: bool = False
    review_status: Optional[str] = None


def _source_out(r: CourseSource) -> SourceOut:
    return SourceOut(
        id=r.id, name=r.name, source_type=r.source_type, uri=r.uri,
        parser=r.parser, status=r.status,
        ingested_at=r.ingested_at.isoformat() if r.ingested_at else None,
        auto_discovered=bool(r.auto_discovered),
        review_status=r.review_status,
    )


@router.get("/sources", response_model=List[SourceOut])
async def list_sources(
    review_status: Optional[str] = Query(None, pattern="^(pending_review|approved|rejected)$"),
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    rows = await crawl_svc.list_sources(db, _read_tenant(user), review_status=review_status)
    return [_source_out(r) for r in rows]


@router.post("/sources", response_model=SourceOut, status_code=201)
async def create_source(
    body: SourceCreate,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """新建课程源。命中合规黑名单直接 400（M5-5 前置阻断）。"""
    try:
        row = await crawl_svc.create_source(
            db,
            tenant_id=_tenant(user),
            name=body.name,
            source_type=body.source_type,
            uri=body.uri,
            parser=body.parser,
            created_by=user.get("user_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(row)
    return _source_out(row)


@router.post("/sources/{source_id}/status", response_model=SourceOut)
async def set_source_status(
    source_id: int,
    status: str = Query(..., pattern="^(active|paused)$"),
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    row = await crawl_svc.set_source_status(db, _tenant(user), source_id, status)
    if row is None:
        raise HTTPException(status_code=404, detail="source not found")
    await db.commit()
    await db.refresh(row)
    return _source_out(row)


class SourceReview(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")


@router.post("/sources/{source_id}/review", response_model=SourceOut)
async def review_source(
    source_id: int,
    body: SourceReview,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M5-7 待审源审核：approve=转 active + 启用草稿采集任务；reject=清理待审源与草稿任务。"""
    if body.action == "approve":
        row = await crawl_disc.approve_source(db, _tenant(user), source_id)
    else:
        ok = await crawl_disc.reject_source(db, _tenant(user), source_id)
        if not ok:
            raise HTTPException(status_code=404, detail="auto-discovered source not found")
        await db.commit()
        return SourceOut(id=source_id, name="", source_type="", uri="", parser="", status="deleted")
    if row is None:
        raise HTTPException(status_code=404, detail="auto-discovered source not found")
    await db.commit()
    await db.refresh(row)
    return _source_out(row)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    user=Depends(require_role(Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(CourseSource).where(
                CourseSource.id == source_id, CourseSource.tenant_id == _tenant(user)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="source not found")
    await db.delete(row)
    await db.commit()


# ------------------------------------------------------------------ 采集任务
class JobCreate(BaseModel):
    name: str
    source_id: Optional[int] = None
    path: str = "P2"
    schedule: Optional[str] = None
    budget_limit: int = Field(default=100, ge=1)
    #: M5-7 自主发现任务：缺省 harvest；discovery 表示定时搜外部资源建待审源
    job_kind: str = "harvest"
    #: M5-7 自主发现检索约束（学科/年级/教材版本等自然语言）
    query: Optional[str] = None


class JobOut(BaseModel):
    id: int
    name: str
    source_id: Optional[int] = None
    path: str
    schedule: Optional[str] = None
    budget_limit: int
    spent: int
    status: str
    next_run_at: Optional[str] = None
    #: M5-7 任务类型与检索约束
    job_kind: Optional[str] = "harvest"
    query: Optional[str] = None
    # M4-4：最近一次 P1 采集建出的卷（P2 / 未跑过为空）
    last_paper_id: Optional[int] = None
    last_run_status: Optional[str] = None
    last_run_error: Optional[str] = None


def _job_out(r: CrawlJob, run: Optional[CrawlRun] = None) -> JobOut:
    return JobOut(
        id=r.id, name=r.name, source_id=r.source_id, path=r.path,
        schedule=r.schedule, budget_limit=r.budget_limit, spent=r.spent,
        status=r.status,
        next_run_at=r.next_run_at.isoformat() if r.next_run_at else None,
        job_kind=r.job_kind, query=r.query,
        last_paper_id=run.paper_id if run is not None else None,
        last_run_status=run.status if run is not None else None,
        last_run_error=run.error if run is not None else None,
    )


@router.get("/jobs", response_model=List[JobOut])
async def list_jobs(
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    # 平台级角色跨租户只读：tenant 占位 platform 在库里没有任何采集任务，
    # 照常过滤会让超管的采集任务页永远空白。
    tenant_id = _read_tenant(user)
    stmt = select(CrawlJob)
    if tenant_id is not None:
        stmt = stmt.where(CrawlJob.tenant_id == tenant_id)
    rows = (await db.execute(stmt.order_by(CrawlJob.id.desc()))).scalars().all()
    return [_job_out(r) for r in rows]


@router.post("/jobs", response_model=JobOut, status_code=201)
async def create_job(
    body: JobCreate,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await crawl_svc.create_job(
            db,
            tenant_id=_tenant(user),
            name=body.name,
            source_id=body.source_id,
            path=body.path,
            schedule=body.schedule,
            budget_limit=body.budget_limit,
            job_kind=body.job_kind,
            query=body.query,
            created_by=user.get("user_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(row)
    return _job_out(row)


@router.post("/jobs/{job_id}/status", response_model=JobOut)
async def set_job_status(
    job_id: int,
    status: str = Query(..., pattern="^(active|paused|stopped)$"),
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M3-8：启用/暂停采集任务。只有 active 才会被调度循环取到。"""
    try:
        row = await crawl_svc.set_job_status(db, _tenant(user), job_id, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    await db.commit()
    await db.refresh(row)
    return _job_out(row)


@router.post("/jobs/{job_id}/run", response_model=JobOut)
async def run_job(
    job_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """手动执行一次（M3-8 验收用：配一个每分钟 job 也能立刻看到结果）。"""
    job = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.id == job_id, CrawlJob.tenant_id == _tenant(user)
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    run = await crawl_svc.run_job(db, job, origin="manual")
    await db.commit()
    await db.refresh(job)
    return _job_out(job, run)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: int,
    user=Depends(require_role(Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M3-8：删除采集任务（连同其运行日志，避免孤儿 run 记录）。"""
    job = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.id == job_id, CrawlJob.tenant_id == _tenant(user)
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await db.execute(
        delete(CrawlRun).where(
            CrawlRun.job_id == job_id, CrawlRun.tenant_id == _tenant(user)
        )
    )
    await db.delete(job)
    await db.commit()


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: int,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    run = await crawl_svc.retry_run(db, _tenant(user), job_id)
    await db.commit()
    job = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.id == job_id, CrawlJob.tenant_id == _tenant(user)
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(job, run)


@router.post("/runs/{run_id}/rollback")
async def rollback_run(
    run_id: int,
    user=Depends(require_role(Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M4-4 一键回退：撤销本次采集新建的草稿题与整卷。

    只删 ``status=draft`` 且 ``source_tag=auto_crawl`` 的题：教师审过、改过的题
    要留下——回退是撤「这次误采」，不是抹掉别人的工作。
    """
    out = await crawl_intake.rollback_run(
        db, tenant_id=_tenant(user), run_id=run_id
    )
    await db.commit()
    return out


@router.get("/metrics")
async def crawl_metrics(
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M5-6：成功率 / 去重率 / 最近运行日志（含 M4-4 产出的卷 id）。"""
    return await crawl_svc.run_metrics(db, _read_tenant(user))


# ------------------------------------------------------------------ 解析 pipeline（M5-2）
class ParseRequest(BaseModel):
    text: str
    source_type: str = "textbook"     # textbook/outline/video/web/bank
    subject: Optional[str] = None


@router.post("/parse")
async def parse_source(
    body: ParseRequest,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
):
    """M5-2：章节切分 -> 知识点候选，输出结构化大纲（纯函数，不落库）。"""
    return course_pipeline.parse_outline(body.text, source_type=body.source_type)


# ------------------------------------------------------------------ 知识点审核（M5-3/4）
class KpUpsert(BaseModel):
    # kp_node.code/parent_code 是 String(32)：在入口挡住超长码，避免 DB 抛
    # DataError 变成 500（B 端联调 2026-11-11：解析结果自带 ≤32 的稳定 code）。
    code: str = Field(..., min_length=1, max_length=32)
    name: str
    parent_code: Optional[str] = Field(default=None, max_length=32)
    subject: Optional[str] = None
    grade: Optional[str] = None
    source: str = "llm"


class KpReview(BaseModel):
    status: str = Field(pattern="^(draft|published)$")


@router.post("/kp-nodes")
async def upsert_kp_nodes(
    items: List[KpUpsert],
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M5-3：抽取结果入库，新节点默认 draft，已存在的 code 不覆盖。

    2026-11-11 B 端联调修复：此前漏了 ``commit``，服务层返回 ``created=1``
    但 get_db 的会话在请求结束时直接 close（未提交的写入被回滚），
    页面上「导入成功」后 draft 列表依旧为空、点审核 404。
    """
    out = await course_pipeline.upsert_kp_nodes(
        db, tenant_id=_tenant(user), items=[i.model_dump() for i in items]
    )
    await db.commit()
    return out


@router.get("/kp-nodes")
async def list_kp_nodes(
    status: Optional[str] = Query(None, pattern="^(draft|published)$"),
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _read_tenant(user)
    stmt = select(KpNode)
    if tenant_id is not None:
        stmt = stmt.where(KpNode.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(KpNode.status == status)
    rows = (await db.execute(stmt.order_by(KpNode.code))).scalars().all()
    return [
        {
            "id": r.id, "code": r.code, "name": r.name, "parent_code": r.parent_code,
            "subject": r.subject, "grade": r.grade, "source": r.source,
            "status": r.status,
        }
        for r in rows
    ]


@router.post("/kp-nodes/{code}/review")
async def review_kp_node(
    code: str,
    body: KpReview,
    user=Depends(require_role(Role.TEACHER, Role.ADMIN, Role.SCHOOL)),
    db: AsyncSession = Depends(get_db),
):
    """M5-4：审核通过后写入 ``kb_knowledge_points`` 供图谱使用；draft 不进学生图谱。"""
    out = await course_pipeline.review_kp_node(
        db, tenant_id=_tenant(user), code=code, status=body.status,
        reviewer=user.get("user_id"),
    )
    if out is None:
        raise HTTPException(status_code=404, detail="kp node not found")
    await db.commit()
    return out
