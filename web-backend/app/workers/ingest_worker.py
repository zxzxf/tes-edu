"""course_kb_ingest 消费端：消费入库消息 → 落库 course_kb。

可随主进程启动（见 main.py lifespan），也可独立运行：
    python -m app.workers.ingest_worker
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

from app.core.db import SessionLocal
from app.core.kafka_client import consume_ingest
from app.services import course_kb as svc

logger = logging.getLogger("ingest_worker")


async def handle_rows(rows: List[dict]) -> None:
    """消费一条消息（一批题目）并幂等落库。"""
    if not rows:
        return
    async with SessionLocal() as db:
        report = await svc.upsert_questions(rows, db)
    logger.info("ingested rows=%s report=%s", len(rows), report)


async def run() -> None:
    logger.info("ingest worker starting...")
    await consume_ingest(handle_rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
