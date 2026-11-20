"""渠道适配器：dispatch 消费后的实际投递。插件式——新渠道=新适配器类。

本期实现两种（计划书 v1.1 定稿）：
- app_offline：消息即"投"（PG 行已存在），投递动作=Redis 未读计数/ZSet 热缓存
  + pub/sub 唤醒在线长轮询；计数与热缓存全部可从 PG 重建，Redis 挂=降级无感。
- wechat_oa：微信公众号推送，PushPlus 免费渠道，WechatOAProvider 抽象——
  将来换自持认证服务号只替换 provider 实现，这里不动。

vendor（极光/厂商通道）、webhook 仅预留占位：dispatcher 遇之记
skipped_unknown_channel 留痕，不炸整条消息。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.config import settings
from app.models.push_hub import PushApp, PushChannel, PushDeliveryLog, PushMessage

logger = logging.getLogger("push_hub.channels")

PUSHPLUS_SEND_URL = "https://www.pushplus.plus/send"


def unread_key(app_id: int, receiver: str) -> str:
    return f"push:unread:{app_id}:{receiver}"


def topic_unread_key(app_id: int, receiver: str, topic_code: str) -> str:
    return f"push:unread:{app_id}:{receiver}:{topic_code}"


def box_key(app_id: int, receiver: str) -> str:
    return f"push:box:{app_id}:{receiver}"


def unread_agg_key(receiver: str) -> str:
    """收件箱未读总数聚合键（push_inbox._unread_count 读它）。"""
    return f"push:unread:agg:{receiver}"


async def bump_inbox_counters(app_id: int, receiver: str, topic_code: str, msg_id: str) -> None:
    """收件箱热层：未读计数 + 最近列表（ZSet score=时间戳，50 条封顶）。

    全部静默降级——Redis 不在就什么都不做，读路径自会回源 PG（计划书 §八）。
    """
    c = cache._get_client()  # noqa: SLF001 与 tests/test_exam_cache.py 同款注入点
    if c is None:
        return
    try:
        await c.incr(unread_key(app_id, receiver))
        await c.incr(topic_unread_key(app_id, receiver, topic_code))
        # agg 键由 push_inbox._unread_count 以 PG count 回源写入；这里只 INCR
        # 了 per-app 键，若不失效 agg，新消息到达后未读总数会停在旧值（实测脏读）。
        # 删除让下次读回源 PG（计划书「PG 为准、Redis 加速」口径）。
        await c.delete(unread_agg_key(receiver))
        score = datetime.now(timezone.utc).timestamp()
        await c.zadd(box_key(app_id, receiver), {msg_id: score})
        await c.zremrangebyrank(box_key(app_id, receiver), 0, -51)
        await c.publish(f"push:ch:{app_id}:{receiver}", msg_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("inbox 热层更新失败（降级直查 DB）：%s", exc)


# ---------------------------------------------------------------- wechat_oa
class WechatOAProvider:
    """微信公众号推送抽象。实现方 send() 返回 (result_code, detail)，异常自吞。"""

    async def send(self, receiver: str, title: str, body: str) -> tuple[str, str]:  # pragma: no cover
        raise NotImplementedError


class PushPlusProvider(WechatOAProvider):
    """PushPlus 微信公众号免费渠道。receiver = 收件人的 pushplus 用户 token。

    命名空间说明：pushplus token 与本系统 push_token（pth-）毫无关系——这里它是
    "渠道侧收件地址"，正如 app_offline 的 receiver 是学生用户 ID。
    """

    def __init__(self, channel_token: str):
        self._token = channel_token

    async def send(self, receiver: str, title: str, body: str) -> tuple[str, str]:
        payload = {"token": self._token, "title": title, "content": body, "channel": "wechat"}
        # receiver 与渠道 token 相同、或为渠道级占位 SELF=推自己（一对一）；
        # 其他值=好友令牌，走 to 转发（pushplus 好友消息语义：to=已加好友的
        # 32 位令牌，多个逗号分隔）。
        # SELF 判定大小写不敏感（11-20 实测：股票盯盘页传小写 self 被当好友
        # 令牌走 to 转发 → pushplus 999 → 整条 dead），在此单点归一，
        # 所有调用方（渠道测试/盯盘预警/open send）共用同一语义。
        rcv = (receiver or "").strip()
        if rcv and rcv != self._token and rcv.upper() != "SELF":
            payload["to"] = rcv
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(PUSHPLUS_SEND_URL, json=payload)
            data = resp.json() if resp.content else {}
            code = str(data.get("code", resp.status_code))
            if code == "200":
                return "ok", ""
            return f"pushplus_{code}", str(data.get("msg", ""))[:480]
        except httpx.TimeoutException:
            return "timeout", "PushPlus 10s 超时"
        except Exception as e:  # noqa: BLE001
            return "error", str(e)[:480]


async def load_wechat_provider(db: AsyncSession) -> Optional[PushPlusProvider]:
    """从 push_channel 读 wechat_oa 配置（Fernet 密文 token）。未配/停用返回 None。"""
    row = (
        await db.execute(select(PushChannel).where(PushChannel.channel_type == "wechat_oa"))
    ).scalar_one_or_none()
    if row is None or not row.enabled or not row.config:
        return None
    from app.services.push_hub import security

    try:
        plain = security.dec_channel_config(row.config.get("enc", ""))
        cfg = json.loads(plain)
    except Exception as e:  # noqa: BLE001 密钥不符/密文坏——渠道不可用但不炸消息
        logger.warning("wechat_oa 渠道凭证解密失败（渠道降级）：%s", e)
        return None
    token = cfg.get("pushplus_token", "")
    return PushPlusProvider(token) if token else None


# ---------------------------------------------------------------- 分发入口
async def deliver(db: AsyncSession, msg_id: str) -> None:
    """worker（Kafka 消费）与 emit 就地降级共用：按 msg_id 完成全渠道投递。

    幂等：msg 不存在→忽略；非 pending 终态→直接返回（Kafka 重放天然安全）。
    """
    from app.services.push_hub import routing

    msg = (await db.execute(select(PushMessage).where(PushMessage.msg_id == msg_id))).scalar_one_or_none()
    if msg is None:
        logger.warning("deliver：msg_id=%s 不存在（消息未落库即消费？）", msg_id)
        return
    if msg.status != "pending":
        return  # 重复消费/已终态：幂等出口

    now = datetime.now(timezone.utc)
    if msg.expire_at is not None:
        exp = msg.expire_at if msg.expire_at.tzinfo else msg.expire_at.replace(tzinfo=timezone.utc)
        if now >= exp:
            msg.status = "expired"
            db.add(PushDeliveryLog(msg_id=msg_id, channel_type="-", result_code="expired"))
            await db.commit()
            return

    app = (await db.execute(select(PushApp).where(PushApp.app_id == msg.app_id))).scalar_one_or_none()
    if app is None:
        msg.status = "dead"
        db.add(PushDeliveryLog(msg_id=msg_id, channel_type="-", result_code="app_missing"))
        await db.commit()
        return

    try:
        topic = await routing.get_active_topic(db, app.app_id, msg.topic_code)
    except (routing.AppDisabled, routing.TopicNotFound, routing.TopicDisabled):
        # 发送后应用/主题被禁用：消息作废，渠道不投（离线不丢≠禁用后还投）
        msg.status = "dead"
        db.add(PushDeliveryLog(msg_id=msg_id, channel_type="-", result_code="topic_or_app_inactive"))
        await db.commit()
        return

    channels = await routing.resolve_channels(db, app, topic, msg.receiver, msg.channel_types)
    codes = [await _deliver_channel(db, msg, c, topic) for c in channels]

    if not codes:
        msg.status = "sent"  # 解析后无渠道（全静音）：app_offline 本体已在 PG，视为完成
    elif all(c == "ok" for c in codes):
        msg.status = "sent"
    elif any(c == "ok" for c in codes):
        msg.status = "partial"
    else:
        msg.status = "dead"
    await db.commit()


async def _deliver_channel(db: AsyncSession, msg: PushMessage, ch_type: str, topic) -> str:
    """单渠道投递 + 留痕。返回 result_code。"""
    detail: Optional[str] = None
    if ch_type == "app_offline":
        await bump_inbox_counters(msg.app_id, msg.receiver, msg.topic_code, msg.msg_id)
        code = "ok"
    elif ch_type == "wechat_oa":
        provider = await load_wechat_provider(db)
        if provider is None:
            code = "skipped_channel_off"
            detail = "wechat_oa 渠道未配置或已停用"
        else:
            code, err = await provider.send(msg.receiver, msg.title, msg.body)
            detail = err or None
    else:
        code = "skipped_unknown_channel"
        detail = f"{ch_type} 适配器未实现（vendor/webhook 预留位）"
    db.add(PushDeliveryLog(
        msg_id=msg.msg_id, channel_type=ch_type,
        result_code=code, detail=(detail or "")[:512] or None,
    ))
    await db.commit()
    return code
