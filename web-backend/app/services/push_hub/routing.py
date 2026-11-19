"""渠道路由解析：一条消息该走哪些渠道、是否被静音。

优先级（计划书 v1.2 定稿）：
  消息显式 channel_types > 收件人订阅 channel_override > 主题 default_channels > 应用 allowed_channels
静音规则：
  - 收件人对主题 muted=True → app_offline 计数跳过（消息仍落库可查，不删）；
  - 主题的 force_channels 内渠道无视静音（运营公告强制微信触达）。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_hub import PushApp, PushSubscription, PushTopic

#: 本期实现的两种渠道（vendor/webhook 在 channels.py 只留占位）
VALID_CHANNELS = ("wechat_oa", "app_offline")


class TopicNotFound(Exception):
    """未注册主题——OpenAPI 回 422，杜绝第三方乱发。"""


class TopicDisabled(Exception):
    pass


class AppDisabled(Exception):
    pass


async def get_active_topic(db: AsyncSession, app_id: int, topic_code: str) -> PushTopic:
    """主题注册制校验：不存在/停用/应用禁用分别抛类型化异常。"""
    app = (await db.execute(select(PushApp).where(PushApp.app_id == app_id))).scalar_one_or_none()
    if app is None or not app.enabled:
        raise AppDisabled(f"应用 {app_id} 不存在或已禁用")
    topic = (
        await db.execute(
            select(PushTopic).where(
                PushTopic.app_id == app_id, PushTopic.topic_code == topic_code
            )
        )
    ).scalar_one_or_none()
    if topic is None:
        raise TopicNotFound(f"主题 {topic_code} 未在应用 {app_id} 注册")
    if not topic.enabled:
        raise TopicDisabled(f"主题 {topic_code} 已停用")
    return topic


async def resolve_channels(
    db: AsyncSession,
    app: PushApp,
    topic: PushTopic,
    receiver: str,
    explicit: Optional[list[str]] = None,
) -> list[str]:
    """解析该消息的触达渠道列表（顺序即投递顺序）。"""
    if explicit:
        channels = [c for c in explicit if c in VALID_CHANNELS]
    else:
        sub = (
            await db.execute(
                select(PushSubscription).where(
                    PushSubscription.app_id == app.app_id,
                    PushSubscription.receiver == receiver,
                    PushSubscription.topic_code == topic.topic_code,
                )
            )
        ).scalar_one_or_none()
        if sub is not None and sub.channel_override:
            channels = [c for c in sub.channel_override if c in VALID_CHANNELS]
        else:
            channels = [c for c in (topic.default_channels or []) if c in VALID_CHANNELS]
            if not channels:
                channels = [c for c in (app.allowed_channels or []) if c in VALID_CHANNELS]

        # 静音：主题 force_channels 之外的渠道里剔除 app_offline 计数
        if sub is not None and sub.muted:
            forced = set(topic.force_channels or [])
            if "app_offline" not in forced:
                channels = [c for c in channels if c != "app_offline"]
    return channels
