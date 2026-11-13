"""运维引导脚本：在 106 生产库建立「课程源定时自主发现」任务（M5-7）。

用途（一次性 / 可重复执行，幂等）：
  在 web-backend 容器内运行，按环境变量批量建立 ``job_kind=discovery`` 的定时任务
  （默认每晚 22:00 触发），并激活它们，使其被调度循环 ``scheduler_loop`` 捞到执行。

为什么不用 B 端 UI：UI 也能建（作业类型选 discovery + 填 query + 设 cron），但
脚本适合上线初始化与回滚重放，且保证幂等（同名任务不会重复建）。

运行方式（在 137 上）：
  docker cp tools/bootstrap_discovery_jobs.py edu-web-backend:/app/bootstrap_discovery_jobs.py
  docker exec -w /app edu-web-backend python /app/bootstrap_discovery_jobs.py
  docker exec edu-web-backend rm -f /app/bootstrap_discovery_jobs.py

环境变量（均可选，含默认值）：
  DISCOVERY_QUERIES   逗号分隔的检索约束；每条建一个发现任务。
                      （2026-11-13 默认："高中数学 真题 解析,高中物理 实验 习题"）
  DISCOVERY_SCHEDULE  5 段 cron；默认 "0 22 * * *"（每晚 22:00）。
  DISCOVERY_TENANT    租户；默认 "school_pilot_01"（与演示联调租户一致）。
  DISCOVERY_BUDGET    budget_limit（仅作任务元数据，discovery 运行不消耗预算）；默认 100。
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.crawl_service import scheduler as crawl_svc
from app.models.ops import CrawlJob


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default).strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


QUERIES = _env_list(
    "DISCOVERY_QUERIES", "高中数学 真题 解析,高中物理 实验 习题"
)
SCHEDULE = os.getenv("DISCOVERY_SCHEDULE", "0 22 * * *").strip()
TENANT = os.getenv("DISCOVERY_TENANT", "school_pilot_01").strip()
BUDGET = int(os.getenv("DISCOVERY_BUDGET", "100") or "100")


def _job_name(query: str) -> str:
    return f"自主发现｜{query}"


async def main() -> int:
    # 合法性前置校验：cron 非法直接报错，避免建出永不触发的任务。
    if crawl_svc.parse_cron(SCHEDULE) is None:
        print(f"[ERROR] DISCOVERY_SCHEDULE={SCHEDULE!r} 不是合法 5 段 cron，中止")
        return 1

    created = skipped = activated = 0
    async with SessionLocal() as db:
        for query in QUERIES:
            name = _job_name(query)
            existing = (
                await db.execute(
                    select(CrawlJob).where(
                        CrawlJob.tenant_id == TENANT, CrawlJob.name == name
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                print(f"[SKIP ] 已存在：{name} (id={existing.id}, status={existing.status})")
                skipped += 1
                # 若已存在但被暂停，仍确保激活（除非人为 stopped）
                if existing.status == "paused":
                    await crawl_svc.set_job_status(db, TENANT, existing.id, "active")
                    activated += 1
                continue
            job = await crawl_svc.create_job(
                db,
                tenant_id=TENANT,
                name=name,
                source_id=None,
                path="P2",
                schedule=SCHEDULE,
                budget_limit=BUDGET,
                job_kind="discovery",
                query=query,
                created_by="bootstrap",
            )
            # create_job 默认 paused；激活后才会进调度循环并由 cron 触发
            await crawl_svc.set_job_status(db, TENANT, job.id, "active")
            await db.commit()
            created += 1
            activated += 1
            print(
                f"[OK   ] 新建并激活：{name} (id={job.id}, "
                f"next_run_at={job.next_run_at})"
            )

    print(
        f"\n汇总：新建 {created} / 跳过已存在 {skipped} / 激活 {activated} "
        f"（cron={SCHEDULE}，tenant={TENANT}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
