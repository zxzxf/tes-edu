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
from app.services import mastery as mastery_svc

logger = logging.getLogger("student_event_worker")


async def handle_events(events) -> None:
    """消费：事件流水 + 步骤级错因流水 + 知识点掌握度更新（沿 DAG）。"""
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
                # 错题本自动写入（T2）：判分错且非过程缺失 → wrong_questions（幂等）
                await learning.record_wrong_question(db, e)
                # 掌握度更新（T-B2）：沿 kb_kp_prerequisites 把整题/步骤对错映射到知识点
                # 未判分（correct=None）时内部自动跳过，只留事件流水。
                await mastery_svc.update_mastery_from_event(db, e)
            logger.info(
                "student_event recorded: student=%s type=%s kps=%d error_type=%s src=%s",
                e.get("student_id"),
                e.get("event_type"),
                len(e.get("knowledge_points") or []),
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
