"""学生收件箱 API（挂载前缀 /api/student，push 域子路由）。

身份铁律（同 student_learning）：receiver 一律取网关注入的 X-User-Id，
**忽略任何请求参数里的"别人"**；范围限定平台自有应用（owner_account_id
IS NULL）——第三方应用即使误投同一 ID 也不进学生收件箱。

未读口径（计划书 §五）：
- 读列表时未读数以 PG `read_at IS NULL` count 为**准**（漂移校准），
  Redis 命中只作加速；read/read-all 直接 DEL 计数键回源，不做 DECR 防漂移。
- 过期消息（expire_at）与 dead 状态不进收件箱。

长轮询 poll：服务端挂起上限 settings.push_poll_max_wait（默认 25s，
**必须 < Cloudflare 边缘 30s**——判分 502 事故红线），期间每 2s 查一次
新消息；无新消息空手而归，客户端循环续拉。游标用**微秒**（since_us，与
DB 存储精度一致）——毫秒截断会让同条消息在下一轮 poll 重复命中。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.config import settings
from app.core.db import get_db
from app.models.push_hub import PushApp, PushMessage, PushSubscription, PushTopic

router = APIRouter(prefix="/push", tags=["push-inbox"])

# poll 内层查询间隔：2s 一查，25s 窗口最多 13 次轻量 exists 查询
_POLL_TICK = 2.0

INBOX_STATUSES = ("pending", "sent", "partial")  # dead/expired 不展示


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms(dt: Optional[datetime]) -> int:
    u = _to_utc(dt)
    return int(u.timestamp() * 1000) if u else 0


def _us(dt: Optional[datetime]) -> int:
    """微秒游标：与 DB 存储精度一致（poll 防重复命中，见 poll docstring）。"""
    u = _to_utc(dt)
    return int(u.timestamp() * 1_000_000) if u else 0


async def _receiver(
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
) -> str:
    if not x_user_id:
        raise HTTPException(401, "缺少学生身份头")
    return x_user_id


async def _platform_app_ids(db: AsyncSession) -> list[int]:
    rows = (
        await db.execute(select(PushApp.app_id).where(PushApp.owner_account_id.is_(None)))
    ).scalars().all()
    return list(rows)


def _inbox_cond(receiver: str, app_ids: list[int]):
    now = datetime.now(timezone.utc)
    return and_(
        PushMessage.receiver == receiver,
        PushMessage.app_id.in_(app_ids or [-1]),
        PushMessage.status.in_(INBOX_STATUSES),
        or_(PushMessage.expire_at.is_(None), PushMessage.expire_at > now),
    )


async def _unread_count(db: AsyncSession, receiver: str, app_ids: list[int]) -> int:
    """未读总数：Redis 加速、PG 校准回源（cache.get_json 静默降级惯例）。"""
    from app.services.push_hub.channels import unread_agg_key
    key = unread_agg_key(receiver)
    cached = await cache.get_json(key)
    if isinstance(cached, int):
        return cached
    n = (
        await db.execute(
            select(func.count())
            .select_from(PushMessage)
            .where(_inbox_cond(receiver, app_ids), PushMessage.read_at.is_(None))
        )
    ).scalar_one()
    await cache.set_json(key, n, 300)
    return n


async def _invalidate(db: AsyncSession, receiver: str, topic_code: Optional[str] = None) -> None:
    from app.services.push_hub.channels import unread_agg_key
    keys = [unread_agg_key(receiver)]
    if topic_code:
        for aid in await _platform_app_ids(db):
            keys += [f"push:unread:{aid}:{receiver}", f"push:unread:{aid}:{receiver}:{topic_code}"]
    else:
        for aid in await _platform_app_ids(db):
            keys.append(f"push:unread:{aid}:{receiver}")
    await cache.delete(*keys)


@router.get("/messages")
async def list_messages(
    receiver: str = Depends(_receiver),
    topic_code: Optional[str] = Query(None),
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    app_ids = await _platform_app_ids(db)
    cond = _inbox_cond(receiver, app_ids)
    if topic_code:
        cond = and_(cond, PushMessage.topic_code == topic_code)
    if unread_only:
        cond = and_(cond, PushMessage.read_at.is_(None))

    total = (
        await db.execute(select(func.count()).select_from(PushMessage).where(cond))
    ).scalar_one()
    rows = (
        await db.execute(
            select(PushMessage).where(cond)
            .order_by(PushMessage.created_at.desc())
            .offset((page - 1) * size).limit(size)
        )
    ).scalars().all()
    # 分主题未读（Tab 角标）：一次 group by
    topic_unread = dict(
        (
            await db.execute(
                select(PushMessage.topic_code, func.count())
                .where(_inbox_cond(receiver, app_ids), PushMessage.read_at.is_(None))
                .group_by(PushMessage.topic_code)
            )
        ).all()
    )
    return {
        "total": total, "page": page, "size": size,
        "unread_total": await _unread_count(db, receiver, app_ids),
        "unread_by_topic": topic_unread,
        "items": [
            {
                "msg_id": m.msg_id, "topic_code": m.topic_code,
                "title": m.title, "body": m.body, "payload": m.payload,
                "read": m.read_at is not None,
                "created_at_ms": _ms(m.created_at),
            }
            for m in rows
        ],
    }


@router.get("/messages/poll")
async def poll(
    since_us: int = Query(..., ge=0, description="上次拉到的最大 created_at 微秒（epoch_us）"),
    receiver: str = Depends(_receiver),
    db: AsyncSession = Depends(get_db),
):
    """长轮询：挂起至多 push_poll_max_wait（<25s，CF 红线）等新消息，到即返回。

    游标=微秒：sqlite/PG 存储都是微秒精度，毫秒截断会让最后一条在下一轮
    `created_at >` 判定中重复命中（测试实测踩过）。
    """
    app_ids = await _platform_app_ids(db)
    since_dt = datetime.fromtimestamp(since_us / 1_000_000, tz=timezone.utc)
    cond = and_(_inbox_cond(receiver, app_ids), PushMessage.created_at > since_dt)

    deadline = settings.push_poll_max_wait
    waited = 0.0
    while True:
        rows = (
            await db.execute(
                select(PushMessage).where(cond).order_by(PushMessage.created_at).limit(50)
            )
        ).scalars().all()
        if rows:
            await _invalidate(db, receiver)
            return {
                "items": [
                    {"msg_id": m.msg_id, "topic_code": m.topic_code, "title": m.title,
                     "body": m.body, "payload": m.payload, "read": False,
                     "created_at_us": _us(m.created_at)}
                    for m in rows
                ],
                "max_created_us": _us(rows[-1].created_at),
            }
        if waited >= deadline:
            return {"items": [], "max_created_us": since_us}
        await asyncio.sleep(_POLL_TICK)
        waited += _POLL_TICK


@router.post("/messages/{msg_id}/read")
async def mark_read(
    msg_id: str,
    receiver: str = Depends(_receiver),
    db: AsyncSession = Depends(get_db),
):
    """只可标自己的消息：receiver≠本人 → 404（不暴露存在性）。"""
    m = (
        await db.execute(
            select(PushMessage).where(PushMessage.msg_id == msg_id, PushMessage.receiver == receiver)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "消息不存在")
    if m.read_at is None:
        m.read_at = datetime.now(timezone.utc)
        await db.commit()
        await _invalidate(db, receiver, m.topic_code)
    return {"msg_id": msg_id, "read": True}


@router.post("/messages/read-all")
async def read_all(
    topic_code: Optional[str] = Query(None),
    receiver: str = Depends(_receiver),
    db: AsyncSession = Depends(get_db),
):
    app_ids = await _platform_app_ids(db)
    cond = and_(_inbox_cond(receiver, app_ids), PushMessage.read_at.is_(None))
    if topic_code:
        cond = and_(cond, PushMessage.topic_code == topic_code)
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(PushMessage).where(cond))).scalars().all()
    for m in rows:
        m.read_at = now
    await db.commit()
    await _invalidate(db, receiver, topic_code)
    return {"marked": len(rows)}


# ---------------------------------------------------------------- 主题静音（App 消息设置）
@router.get("/subscriptions")
async def list_subscriptions(
    receiver: str = Depends(_receiver),
    db: AsyncSession = Depends(get_db),
):
    """收件人的主题静音状态：平台自有应用的全部启用主题都列出（未订阅=未静音）。

    口径与 routing.resolve_channels 完全一致：静音只剔 app_offline 计数，
    消息仍落库可查；主题 force_channels 内的渠道无视静音。
    """
    app_ids = await _platform_app_ids(db)
    if not app_ids:
        return {"items": []}
    topics = (
        await db.execute(
            select(PushTopic.app_id, PushTopic.topic_code, PushTopic.name)
            .where(PushTopic.app_id.in_(app_ids), PushTopic.enabled.is_(True))
            .order_by(PushTopic.topic_code)
        )
    ).all()
    subs = (
        await db.execute(
            select(PushSubscription.app_id, PushSubscription.topic_code, PushSubscription.muted)
            .where(PushSubscription.receiver == receiver, PushSubscription.app_id.in_(app_ids))
        )
    ).all()
    muted_set = {(s.app_id, s.topic_code) for s in subs if s.muted}
    return {
        "items": [
            {"topic_code": t.topic_code, "name": t.name or t.topic_code,
             "muted": (t.app_id, t.topic_code) in muted_set}
            for t in topics
        ]
    }


@router.put("/subscriptions/{topic_code}")
async def set_subscription_muted(
    topic_code: str,
    muted: bool = Query(...),
    receiver: str = Depends(_receiver),
    db: AsyncSession = Depends(get_db),
):
    """设置某主题静音（upsert）。只对平台自有应用主题开放，防越权改第三方配置。"""
    app_ids = await _platform_app_ids(db)
    valid = (
        await db.execute(
            select(PushTopic.app_id).where(
                PushTopic.app_id.in_(app_ids),
                PushTopic.topic_code == topic_code,
                PushTopic.enabled.is_(True),
            )
        )
    ).scalars().all()
    if not valid:
        raise HTTPException(404, "主题不存在或不可设置")
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    for app_id in valid:
        stmt = pg_insert(PushSubscription).values(
            app_id=app_id, receiver=receiver, topic_code=topic_code,
            muted=muted, updated_at=datetime.now(timezone.utc),
        ).on_conflict_do_update(
            index_elements=["app_id", "receiver", "topic_code"],
            set_={"muted": muted, "updated_at": datetime.now(timezone.utc)},
        )
        await db.execute(stmt)
    await db.commit()
    return {"topic_code": topic_code, "muted": muted}
