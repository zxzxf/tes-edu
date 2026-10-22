"""Kafka 异步管道客户端（aiokafka）。

- 生产者：ingest 接口把清洗后的题目投递到 course_kb_ingest。
- 消费者：后台 worker 消费并落库 course_kb。

设计约定：消息体为「已清洗的题目 dict 列表」，消费端直接 upsert，
不再做字段清洗（清洗在入库前由 parser 完成）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, List

from app.core.config import settings

logger = logging.getLogger("kafka")

_producer = None


async def get_producer():
    """懒启动生产者（首次调用才连 broker）。"""
    global _producer
    if _producer is None:
        from aiokafka import AIOKafkaProducer

        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        )
        await _producer.start()
        logger.info("kafka producer started -> %s", settings.kafka_bootstrap)
    return _producer


async def publish_ingest(rows: List[dict], topic: str | None = None) -> None:
    """投递一批已清洗的题目到入库主题。"""
    prod = await get_producer()
    await prod.send_and_wait(topic or settings.kafka_topic_ingest, rows)
    logger.info("published %d rows -> %s", len(rows), topic or settings.kafka_topic_ingest)


async def consume_ingest(
    handler: Callable[[List[dict]], Awaitable[Any]],
    topic: str | None = None,
    group_id: str | None = None,
) -> None:
    """常驻消费：逐条消息交给 handler，单条失败不影响后续。"""
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        topic or settings.kafka_topic_ingest,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=group_id or settings.kafka_group,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    logger.info(
        "kafka consumer started: topic=%s group=%s",
        topic or settings.kafka_topic_ingest,
        group_id or settings.kafka_group,
    )
    try:
        async for msg in consumer:
            rows = msg.value
            if isinstance(rows, dict):
                rows = [rows]
            try:
                await handler(rows)
            except Exception:  # noqa: BLE001 单条失败不中断消费
                logger.exception("consume failed, skip message")
    finally:
        await consumer.stop()


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None
        logger.info("kafka producer stopped")
