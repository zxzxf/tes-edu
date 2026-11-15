from app.core.db import SessionLocal
from sqlalchemy import select
from app.models.ops import CourseSource, CrawlJob
import asyncio

async def m():
    async with SessionLocal() as s:
        src = (await s.execute(
            select(CourseSource).where(CourseSource.name.like('%高中数学真题库%高考网%'))
        )).scalars().all()
        for x in src:
            print(f"src#{x.id} name={x.name!r} tenant={x.tenant_id!r} auto_discovered={x.auto_discovered} status={x.status!r} review={x.review_status!r} uri={x.uri!r}")
        print("=== 绑定该源的全部任务 ===")
        for x in src:
            js = (await s.execute(
                select(CrawlJob).where(CrawlJob.source_id == x.id).order_by(CrawlJob.id)
            )).scalars().all()
            for j in js:
                print(f"  job#{j.id} kind={j.job_kind} path={j.path} status={j.status} name={j.name!r} last_paper={j.last_paper_id}")

asyncio.run(m())
