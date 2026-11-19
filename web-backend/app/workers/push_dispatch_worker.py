"""push_dispatch 消费端：按 msg_id 完成渠道路由与投递分发。

可随主进程启动（main.py lifespan，仅 kafka_enabled 时），也可独立运行：
    python -m app.workers.push_dispatch_worker

消费语义（计划书 §八）：
- Kafka at-least-one → 重复 envelope 由 msg 终态幂等出口吞掉（channels.deliver 头部）；
- 单条失败不中断消费（照抄 student_event_worker），失败消息保持 pending，
  等下一次重放或人工经 admin 重发；
- 本进程无 Kafka（emit 降级路径）时不启动——降级就地分发，无需消费端。
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.kafka_client import consume_ingest
from app.services.push_hub import channels as ch

logger = logging.getLogger("push_dispatch_worker")


async def handle_envelopes(envelopes) -> None:
    if isinstance(envelopes, dict):
        envelopes: List[dict] = [envelopes]
    for env in envelopes:
        if not isinstance(env, dict):
            continue
        msg_id = env.get("msg_id")
        if not msg_id:
            continue
        try:
            async with SessionLocal() as db:
                await ch.deliver(db, msg_id)
        except Exception:  # noqa: BLE001 单条失败不中断消费；保持 pending 待重放
            logger.exception("push_dispatch 处理失败，跳过 msg=%s", msg_id)


async def run() -> None:
    logger.info("push_dispatch worker starting... topic=%s", settings.kafka_topic_push_dispatch)
    await consume_ingest(
        handle_envelopes,
        topic=settings.kafka_topic_push_dispatch,
        group_id=settings.kafka_group_push_dispatch,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
