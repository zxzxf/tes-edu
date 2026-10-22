"""学情事件上报（student_event）。

把 /solve、/solve_image 的交互异步投递到 Kafka，供下游做学情/错题/掌握度分析。

三条硬约束：
1. 未配置 KAFKA_BOOTSTRAP 时直接跳过，不影响任何功能。
2. 上报失败只记日志，**绝不因埋点让解题接口失败**。
3. 上报走后台任务，不阻塞响应（避免 Kafka 抖动拖慢解题）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core import config

logger = logging.getLogger("events")

_producer = None
_pending: set[asyncio.Task] = set()


async def _get_producer():
    global _producer
    if _producer is None:
        from aiokafka import AIOKafkaProducer  # 延迟导入：未启用时无需安装

        _producer = AIOKafkaProducer(
            bootstrap_servers=config.KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        )
        await _producer.start()
    return _producer


async def emit_student_event(event: dict[str, Any]) -> bool:
    """投递一条学情事件；返回是否成功（失败只记日志）。"""
    if not config.kafka_ready():
        return False
    try:
        prod = await asyncio.wait_for(_get_producer(), timeout=5)
        payload = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        await asyncio.wait_for(
            prod.send_and_wait(config.KAFKA_TOPIC_EVENT, payload), timeout=5
        )
        return True
    except Exception as e:  # noqa: BLE001 埋点失败不影响主链路
        logger.warning("student_event 上报失败（不影响解题）：%s", e)
        return False


def emit_student_event_bg(event: dict[str, Any]) -> None:
    """后台上报，不阻塞当前请求。"""
    if not config.kafka_ready():
        return
    task = asyncio.create_task(emit_student_event(event))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def shutdown() -> None:
    """关闭：给待发事件一点时间，再停生产者。"""
    if _pending:
        await asyncio.wait(list(_pending), timeout=5)
    global _producer
    if _producer is not None:
        try:
            await _producer.stop()
        except Exception:  # noqa: BLE001
            pass
        _producer = None
