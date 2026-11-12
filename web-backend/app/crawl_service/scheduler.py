"""M3-8 新题定时采集 / M5-1 课程源 / M5-5 合规与预算 / M5-6 可观测。

拆成两层：
  * 纯函数：cron 匹配、预算判定、合规校验（不连库，可单测）；
  * DB 薄层：任务 CRUD、执行采集、写运行日志与指标。

cron 为什么自己实现（不引 APScheduler）
---------------------------------------
只需要「5 段标准 cron、分钟级、单进程」这一种场景；引一个调度框架会带来
后台线程、时钟漂移与重复触发问题，而线上同一时刻只应有一个采集 worker。
这里只做匹配（``matches_cron``）与「下一次触发时间」（``next_run_at``），
触发由调用方（启动期循环 / 手动 POST）负责，进程内同一 job 用行锁串行化。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_kb import CourseQuestion
from app.models.ops import CrawlJob, CrawlRun, CourseSource

#: 入库逻辑（去重 / P1 整卷 / P2 拆题 / 回退）归属采集域，随子包一起抽离。
from app.crawl_service import intake as crawl_intake  # noqa: E402

log = logging.getLogger(__name__)

#: 采集源抓取与解析（让「采集任务」真正能采：从源读内容 -> 解析成 items）。
from app.crawl_service import fetcher as crawl_fetch  # noqa: E402

#: M5-2：非结构化网页 -> 结构化题目的 LLM 抽取（默认关，见 config）。
from app.crawl_service import llm_extract  # noqa: E402

#: 版权/内容合规黑名单（M5-5）。业务给正式清单前，这里只放「明确不可采集」的源，
#: 命中即阻断——**默认放行**，否则任何新源都要等人工改代码才能用。
COMPLIANCE_BLOCKLIST: Tuple[str, ...] = (
    "copyright-reserved.example",
    "禁止转载",
    "未经授权",
)

#: 采集路径（M4-4）
PATH_P1 = "P1"   # 整卷入库：成套卷直接落 exam_paper(source=auto_crawl)
PATH_P2 = "P2"   # 拆题入库：进 course_kb，再由规则组卷重组


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ 纯函数
def parse_cron(expr: str) -> Optional[Tuple[set, set, set, set, set]]:
    """解析 5 段 cron（分 时 日 月 周），支持 ``*`` / ``*/n`` / ``a-b`` / 逗号枚举。

    不支持 ``?``/``L``/``W`` 等扩展语法——线上用不到，静默支持反而让人以为能用。
    返回 None 表示非法表达式（调用方据此拒绝保存，避免存进去了却永远不触发）。
    """
    parts = (expr or "").split()
    if len(parts) != 5:
        return None

    def _range(spec: str, lo: int, hi: int) -> Optional[set]:
        out: set = set()
        for piece in spec.split(","):
            piece = piece.strip()
            if not piece:
                return None
            if piece == "*":
                out |= set(range(lo, hi + 1))
                continue
            if piece.startswith("*/"):
                try:
                    step = int(piece[2:])
                except ValueError:
                    return None
                if step <= 0:
                    return None
                out |= set(range(lo, hi + 1, step))
                continue
            if "-" in piece:
                a, _, b = piece.partition("-")
                try:
                    start, end = int(a), int(b)
                except ValueError:
                    return None
                if not (lo <= start <= end <= hi):
                    return None
                out |= set(range(start, end + 1))
                continue
            try:
                value = int(piece)
            except ValueError:
                return None
            if not (lo <= value <= hi):
                return None
            out.add(value)
        return out

    minute = _range(parts[0], 0, 59)
    hour = _range(parts[1], 0, 23)
    day = _range(parts[2], 1, 31)
    month = _range(parts[3], 1, 12)
    # Python weekday: 周一=0；cron: 周日=0/7
    dow_raw = _range(parts[4].replace("7", "0"), 0, 6)
    if None in (minute, hour, day, month, dow_raw):
        return None
    return minute, hour, day, month, dow_raw


def matches_cron(expr: str, when: datetime) -> bool:
    """该时刻是否命中 cron（分钟粒度；秒一律按 0 处理）。"""
    parsed = parse_cron(expr)
    if parsed is None:
        return False
    minute, hour, day, month, dow = parsed
    cron_dow = (when.weekday() + 1) % 7  # 周一=0 -> cron 周一=1
    return (
        when.minute in minute
        and when.hour in hour
        and when.day in day
        and when.month in month
        and cron_dow in dow
    )


def next_run_at(expr: str, after: Optional[datetime] = None) -> Optional[datetime]:
    """下一次触发时刻（最多向后找 366 天，找不到返回 None）。"""
    if parse_cron(expr) is None:
        return None
    cursor = (after or _utcnow()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if matches_cron(expr, cursor):
            return cursor
        cursor += timedelta(minutes=1)
    return None


def budget_exceeded(job: CrawlJob) -> bool:
    """预算是否已耗尽（``spent >= budget_limit``）。"""
    limit = int(job.budget_limit or 0)
    if limit <= 0:
        return False
    return int(job.spent or 0) >= limit


def compliance_reject(uri: str) -> Optional[str]:
    """命中合规黑名单时返回原因；放行返回 None（只做保守的前置阻断）。"""
    haystack = (uri or "").lower()
    for word in COMPLIANCE_BLOCKLIST:
        if word.lower() in haystack:
            return f"来源命中合规黑名单「{word}」，禁止采集"
    return None


# ------------------------------------------------------------------ DB 薄层
async def list_sources(db: AsyncSession, tenant_id: str) -> List[CourseSource]:
    return list(
        (
            await db.execute(
                select(CourseSource)
                .where(CourseSource.tenant_id == tenant_id)
                .order_by(CourseSource.id.desc())
            )
        ).scalars().all()
    )


async def create_source(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    source_type: str,
    uri: str,
    parser: str = "auto",
    created_by: Optional[str] = None,
) -> CourseSource:
    reason = compliance_reject(uri)
    if reason:
        raise ValueError(reason)
    row = CourseSource(
        tenant_id=tenant_id,
        name=name,
        source_type=source_type,
        uri=uri,
        parser=parser,
        status="paused",
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def set_source_status(
    db: AsyncSession, tenant_id: str, source_id: int, status: str
) -> Optional[CourseSource]:
    if status not in ("active", "paused"):
        raise ValueError("status 只能是 active/paused")
    row = (
        await db.execute(
            select(CourseSource).where(
                CourseSource.id == source_id, CourseSource.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = status
    await db.flush()
    return row


#: 任务可切换的状态。`stopped` 是预算耗尽后的终态：重新启用必须先把
#: `budget_limit` 调大，否则调度器取到也会立刻停。
JOB_STATUSES = ("active", "paused", "stopped")


async def set_job_status(
    db: AsyncSession, tenant_id: str, job_id: int, status: str
) -> Optional[CrawlJob]:
    """M3-8：启停采集任务。

    新建任务默认 `paused`（不让人一建就定时跑），教师显式启用后才进调度器。
    重新启用时顺带重算 `next_run_at`：否则保存时算出的时间可能早已过期，
    会被当成「每次 tick 都到期」而重复执行。
    """
    if status not in JOB_STATUSES:
        raise ValueError("status 只能是 active/paused/stopped")
    row = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.id == job_id, CrawlJob.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    # 先校验再改状态：否则校验失败会把 row 改成 active 却未 flush，
    # 调用方若捕获异常继续用同一 session 就会拿到一个「以为启用了」的对象。
    next_at = None
    if status == "active" and row.schedule:
        if parse_cron(row.schedule) is None:
            raise ValueError("schedule 不是合法的 5 段 cron，无法启用")
        next_at = next_run_at(row.schedule)
    row.status = status
    if next_at is not None:
        row.next_run_at = next_at
    await db.flush()
    return row


async def create_job(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    source_id: Optional[int],
    path: str,
    schedule: Optional[str],
    budget_limit: int,
    created_by: Optional[str] = None,
) -> CrawlJob:
    if path not in (PATH_P1, PATH_P2):
        raise ValueError("path 只能是 P1/P2")
    if schedule and parse_cron(schedule) is None:
        raise ValueError("schedule 不是合法的 5 段 cron")
    row = CrawlJob(
        tenant_id=tenant_id,
        source_id=source_id,
        name=name,
        path=path,
        schedule=schedule,
        budget_limit=max(1, int(budget_limit or 1)),
        spent=0,
        status="paused",
        created_by=created_by,
        next_run_at=next_run_at(schedule) if schedule else None,
    )
    db.add(row)
    await db.flush()
    return row


async def _resolve_items_from_source(
    db: AsyncSession, job: CrawlJob
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """从采集源抓取内容并解析成题目 items（供 UI「执行一次」/ 定时调度复用）。

    返回 ``(items, reason)``：``reason`` 非空表示未能产出结构化题——要么源抓取
    失败（记 failed），要么是非结构化内容需 LLM 抽取（记 ok + 原因，不再静默 0）。
    """
    if not job.source_id:
        return [], None
    src = (
        await db.execute(
            select(CourseSource).where(
                CourseSource.id == job.source_id,
                CourseSource.tenant_id == job.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if src is None:
        return [], f"采集源 {job.source_id} 不存在或不属于本租户"
    try:
        content = await crawl_fetch.fetch_source_content(src)
    except Exception as exc:  # noqa: BLE001 抓取异常要落进运行日志，不能让任务静默崩
        return [], f"源抓取失败：{exc}"
    items, reason = crawl_fetch.parse_items(content, src.source_type)
    if crawl_fetch.needs_llm(reason):
        # M5-2：HTML/纯文本没有结构化题目，交给 LLM 抽取环节。
        # 默认关闭时 extract_items 会返回「需 LLM 抽取环节：未启用」的原因，
        # 依旧不静默 0 条；开启后失败原因写进 run.error 可追溯。
        items, reason = await llm_extract.extract_items(
            content,
            source_type=src.source_type,
            db=db,
            tenant_id=job.tenant_id,
        )
    if reason:
        return [], reason
    return items, None


async def run_job(
    db: AsyncSession,
    job: CrawlJob,
    *,
    items: Optional[Sequence[Dict[str, Any]]] = None,
) -> CrawlRun:
    """执行一次采集：去重入库 ``draft``，超预算立即停（M3-8）。

    ``items`` 为待入库的题库行（由解析器产出）。为空时只记一条「无新内容」的运行，
    这样调度器照样有可观测日志（M5-6），不会「跑了但什么都没记」。
    """
    run = CrawlRun(job_id=job.id, tenant_id=job.tenant_id, status="running")
    db.add(run)
    await db.flush()

    if budget_exceeded(job):
        run.status = "stopped"
        run.budget_stop = True
        run.finished_at = _utcnow()
        run.error = f"预算已用尽（{job.spent}/{job.budget_limit}），未执行"
        job.status = "stopped"
        return run

    rows: List[Dict[str, Any]] = []
    if items is None:
        # 调用方没显式给 items（UI「执行一次」/ 定时调度都走这里）：
        # 真正从采集源抓取内容并解析，而不是静默记一条 fetched=0 的空运行。
        resolved, reason = await _resolve_items_from_source(db, job)
        if reason:
            run.error = reason
            # 抓取/模型调用失败属真故障（failed）；「需 LLM 抽取环节」标记、
            # 「未启用」「页面无题」属业务性空结果，保持 ok + 原因可追溯。
            if reason.startswith((
                "源抓取失败", "采集源", "不支持的源", "LLM 抽取失败",
            )):
                run.status = "failed"
        rows = resolved
    else:
        rows = list(items)
    run.fetched = len(rows)
    inserted = duplicated = failed = 0
    paper_id = None
    # 预算按「本次实际可入库条数」截断后再进 intake：intake 会一次性建卷，
    # 若先入库再判预算，就会出现「卷已建好、题只进了一半」的破卷。
    allowed: List[Dict[str, Any]] = []
    for row in rows:
        if budget_exceeded(job):
            run.budget_stop = True
            run.status = "stopped"
            job.status = "stopped"
            break
        allowed.append(row)
        job.spent = int(job.spent or 0) + 1

    if allowed:
        try:
            result = await crawl_intake.intake(
                db, job, items=allowed, run_id=run.id,
            )
            inserted = int(result.get("inserted") or 0)
            duplicated = int(result.get("duplicated") or 0)
            paper_id = result.get("paper_id")
        except Exception as exc:  # noqa: BLE001 整批失败要留日志，不能让调度器静默
            failed = len(allowed)
            log.warning("crawl intake failed: %s", exc)

    run.inserted = inserted
    run.duplicated = duplicated
    run.failed = failed
    run.paper_id = paper_id
    run.finished_at = _utcnow()
    if run.status == "running":
        run.status = "ok"
    job.last_run_at = _utcnow()
    job.next_run_at = next_run_at(job.schedule) if job.schedule else None
    if budget_exceeded(job):
        job.status = "stopped"
        run.budget_stop = True
    return run


async def due_jobs(
    db: AsyncSession, now: Optional[datetime] = None
) -> List[CrawlJob]:
    """到期应执行的 active 任务（M3-8 调度器取数）。

    ``next_run_at`` 为空但有 schedule 的任务也纳入：老任务创建时 schedule 可能
    是后补的；由调度器补算下一次触发，不能永远不跑。
    """
    now = now or _utcnow()
    rows = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.status == "active",
                CrawlJob.schedule.isnot(None),
            )
        )
    ).scalars().all()
    out = []
    for job in rows:
        if not (job.schedule or "").strip():
            continue
        next_run = job.next_run_at
        if next_run is None:
            out.append(job)
            continue
        # SQLite 会丢掉 tzinfo：统一按 UTC 比较，避免 naive/aware 抛 TypeError
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        if next_run <= now:
            out.append(job)
    return out


async def run_due_jobs(
    db: AsyncSession, now: Optional[datetime] = None
) -> List[CrawlRun]:
    """跑一轮到期任务；单任务失败不阻断其余任务。"""
    now = now or _utcnow()
    runs: List[CrawlRun] = []
    for job in await due_jobs(db, now):
        try:
            runs.append(await run_job(db, job))
            await db.commit()
        except Exception as exc:  # noqa: BLE001 调度器不能因单任务死掉
            await db.rollback()
            log.warning("crawl job %s failed in scheduler: %s", job.id, exc)
    return runs


async def scheduler_loop(
    *, interval_seconds: int = 60, stop_event: Optional[Any] = None
) -> None:
    """后台调度循环（随 web-backend 启动；单进程单 worker）。

    只做「到点触发」，真正的拉取/解析由 ``run_job(items=...)`` 的调用方提供。
    没配 schedule 的任务永远不会被这里捞到——手动执行仍走 POST /run。
    """
    import asyncio

    from app.core.db import SessionLocal

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            async with SessionLocal() as db:
                await run_due_jobs(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 DB 未就绪等：下轮再试
            log.warning("crawl scheduler tick failed: %s", exc)
        await asyncio.sleep(max(10, int(interval_seconds)))


async def run_metrics(db: AsyncSession, tenant_id: str) -> Dict[str, Any]:
    """M5-6：成功率 / 去重率 / 失败原因分布 + 最近日志（可重试）。"""
    runs = (
        await db.execute(
            select(CrawlRun)
            .where(CrawlRun.tenant_id == tenant_id)
            .order_by(CrawlRun.started_at.desc())
            .limit(200)
        )
    ).scalars().all()
    total = len(runs)
    ok = sum(1 for r in runs if r.status == "ok")
    stopped = sum(1 for r in runs if r.status == "stopped")
    failed = sum(1 for r in runs if r.status == "failed")
    inserted = sum(int(r.inserted or 0) for r in runs)
    duplicated = sum(int(r.duplicated or 0) for r in runs)
    return {
        "runs": total,
        "ok": ok,
        "failed": failed,
        "stopped": stopped,
        "success_rate": round(ok / total, 4) if total else 0.0,
        "inserted": inserted,
        "duplicated": duplicated,
        "dedup_rate": round(duplicated / (inserted + duplicated), 4)
        if (inserted + duplicated)
        else 0.0,
        "recent": [
            {
                "id": r.id,
                "job_id": r.job_id,
                "status": r.status,
                "fetched": r.fetched,
                "inserted": r.inserted,
                "duplicated": r.duplicated,
                "failed": r.failed,
                "budget_stop": r.budget_stop,
                "paper_id": r.paper_id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "error": r.error,
            }
            for r in runs
        ],
    }


async def retry_run(
    db: AsyncSession, tenant_id: str, job_id: int
) -> Optional[CrawlRun]:
    """手动重试：把任务从 stopped 恢复为 active 并立刻跑一次（无新内容时只记日志）。"""
    job = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.id == job_id, CrawlJob.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    if job.status == "stopped" and budget_exceeded(job):
        # 预算用尽时重试只会空转：与暂停区分开，避免给人一种「修好了」的错觉
        run = CrawlRun(
            job_id=job.id, tenant_id=job.tenant_id, status="stopped",
            finished_at=_utcnow(), budget_stop=True,
            error="预算已用尽，重试被拒绝（请先调整 budget_limit）",
        )
        db.add(run)
        await db.flush()
        return run
    job.status = "active"
    return await run_job(db, job)