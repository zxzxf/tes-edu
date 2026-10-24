"""student_event 消费端：落事件流水 + 累计知识点练习量。

可随主进程启动（见 main.py lifespan），也可独立运行：
    python -m app.workers.student_event_worker
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.kafka_client import consume_ingest
from app.services import learning

logger = logging.getLogger("student_event_worker")


async def handle_events(events) -> None:
    """消费：事件流水 + 步骤级错因流水 + 每个知识点的练习量 +1。"""
    if isinstance(events, dict):
        events: List[dict] = [events]
    for e in events:
        if not isinstance(e, dict):
            continue
        try:
            async with SessionLocal() as db:
                await learning.record_event(db, e)
                # judge v2 归因落 student_step_errors（S5 周报/错因分布燃料）
                await learning.record_step_error(db, e)
                kps = e.get("knowledge_points") or []
                # correct 来自 agent-backend 的判分（未提交作答时为 None）
                correct = e.get("correct")
                for kp in kps:
                    await learning.bump_practice(
                        db,
                        student_id=e.get("student_id") or "",
                        knowledge_point=kp,
                        tenant_id=e.get("tenant_id"),
                        subject=e.get("subject"),
                        correct=correct if isinstance(correct, bool) else None,
                    )
            logger.info(
                "student_event recorded: student=%s type=%s kps=%d error_type=%s src=%s",
                e.get("student_id"),
                e.get("event_type"),
                len(kps),
                e.get("error_type"),
                e.get("attribution_source"),
            )
        except Exception:  # noqa: BLE001 单条失败不中断消费
            logger.exception("student_event 处理失败，跳过")


async def run() -> None:
    logger.info("student_event worker starting...")
    await consume_ingest(
        handle_events,
        topic=settings.kafka_topic_event,
        group_id=settings.kafka_group_event,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
