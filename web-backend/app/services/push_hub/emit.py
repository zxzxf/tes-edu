"""推送单点写入口：业务方（平台内/第三方 OpenAPI）都只走 emit()。

链路（计划书 §三）：
  emit → PG push_message(status=pending) 【唯一事实源，先落库再投递=离线不丢】
       → Kafka envelope{msg_id}（可用时）→ dispatch_worker 消费分发
       → Kafka 抖动/不可用 → 本进程内直接 deliver（BackgroundTasks 调用方已是异步，就地分发）

幂等锚点=msg_id 主键：同 msg_id 重复 emit（含 worker 重放）在落库层天然去重。
TTL 优先级：调用方 ttl_seconds > 主题 default_ttl > settings.push_message_default_ttl。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.push_hub import PushMessage
from app.services.push_hub import channels as ch
from app.services.push_hub.routing import AppDisabled, TopicDisabled, TopicNotFound, get_active_topic

logger = logging.getLogger("push_hub.emit")

_pending: set[asyncio.Task] = set()


class DuplicateMsg(Exception):
    """同 msg_id 重复提交——幂等语义，调用方视为成功即可。"""


async def emit(
    db: AsyncSession,
    *,
    app_id: int,
    topic_code: str,
    receiver: str,
    title: str,
    body: str,
    payload: Optional[dict] = None,
    channel_types: Optional[list[str]] = None,
    ttl_seconds: Optional[int] = None,
    token_id: Optional[int] = None,
    msg_id: Optional[str] = None,
) -> str:
    """写一条待推送消息并触发分发。返回 msg_id。

    校验失败抛类型化异常：TopicNotFound(422) / TopicDisabled / AppDisabled(403)——
    路由层翻译，不在这猜 HTTP 码。
    """
    topic = await get_active_topic(db, app_id, topic_code)

    ttl = ttl_seconds or topic.default_ttl or settings.push_message_default_ttl
    now = datetime.now(timezone.utc)
    mid = msg_id or str(uuid.uuid4())
    row = PushMessage(
        msg_id=mid,
        app_id=app_id,
        token_id=token_id,
        topic_code=topic_code,
        receiver=receiver,
        title=title[:128],
        body=body,
        payload=payload,
        channel_types=channel_types,
        expire_at=now + timedelta(seconds=ttl) if ttl else None,
        status="pending",
        created_at=now,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise DuplicateMsg(mid)

    # 分发触发：Kafka 优先，失败就地 deliver（两者语义等价，msg_id 幂等兜底）。
    # 就地路径复用当前会话——消息已 commit，deliver 内部各自 commit。
    if settings.kafka_enabled:
        try:
            from app.core.kafka_client import get_producer

            prod = await asyncio.wait_for(get_producer(), timeout=5)
            await asyncio.wait_for(
                prod.send_and_wait(settings.kafka_topic_push_dispatch, {"msg_id": mid}),
                timeout=5,
            )
            return mid
        except Exception as e:  # noqa: BLE001 Kafka 抖动绝不阻断消息发出
            logger.warning("push envelope 投递失败，就地分发 msg=%s：%s", mid, e)

    await ch.deliver(db, mid)
    return mid


def emit_bg(**kwargs) -> asyncio.Task:
    """BackgroundTasks 场景的 fire-and-forget：自建会话执行 emit，失败只记日志。

    业务主链路（判分/布置）用它——推送是旁路，绝不反压业务。
    """
    async def _run():
        from app.core.db import SessionLocal

        try:
            async with SessionLocal() as db:
                await emit(db, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("emit_bg 失败（业务不受影响）topic=%s receiver=%s",
                             kwargs.get("topic_code"), kwargs.get("receiver"))

    task = asyncio.create_task(_run())
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return task
