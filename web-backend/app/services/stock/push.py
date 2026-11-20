"""预警推送：经平台推送中心（wechat_oa 渠道 → PushPlus），不再直连 PushPlus。

计划书 v1.4 原文直连 pushplus /send + PUSHPLUS_TOKEN 环境变量；实施口径升级
（2026-11-20）：渠道凭证已 Fernet 加密在 push_channel 表、消息统一落
push_message 可审计，盯盘只是推送中心的一个业务方——复用 emit 管道。

receiver 语义（PushPlusProvider.send 现成逻辑）：
- 'SELF'          → 推渠道账号本人（一对一，默认）
- 好友令牌(32hex) → 自动加 to 走好友消息
push_message.receiver 是 String(64)：多好友必须一人一条 emit（v1.4 §七
"合并单次 to" 在推送中心模型下不可行，改为逐条 + 冷却去重兜底限频）。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_hub import PushApp

logger = logging.getLogger("stock.push")

STOCK_APP_NAME = "股票盯盘"
STOCK_TOPIC_CODE = "stock_alert"


async def get_or_create_stock_app(db: AsyncSession) -> PushApp:
    """平台自有应用「股票盯盘」：懒建（首次触达时），allowed 微信+App 离线。"""
    row = (
        await db.execute(
            select(PushApp).where(
                PushApp.owner_account_id.is_(None), PushApp.name == STOCK_APP_NAME
            )
        )
    ).scalars().first()
    if row is not None:
        return row
    row = PushApp(
        name=STOCK_APP_NAME,
        allowed_channels=["wechat_oa", "app_offline"],
        rate_limit_per_min=30,
        enabled=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_or_create_stock_topic(db: AsyncSession, app: PushApp):
    """主题 stock_alert：仅微信渠道（盯盘是 admin 自用提醒，不落学生收件箱），日频控 200。"""
    from app.models.push_hub import PushTopic

    row = (
        await db.execute(
            select(PushTopic).where(
                PushTopic.app_id == app.app_id, PushTopic.topic_code == STOCK_TOPIC_CODE
            )
        )
    ).scalars().first()
    if row is not None:
        return row
    row = PushTopic(
        app_id=app.app_id,
        topic_code=STOCK_TOPIC_CODE,
        name="涨跌预警",
        default_channels=["wechat_oa"],
        rate_limit_per_day=200,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def push_alert(
    db: AsyncSession,
    title: str,
    content: str,
    receivers: list[str],
) -> list[dict]:
    """向接收方列表逐个 emit。返回 [{"receiver":..., "msg_id":..., "status":"dispatched"}]。

    receivers 空 → 视为 ["SELF"]（防御：调用方已做默认解析，这里兜底）。
    任何单条异常不抛出（推送是旁路，绝不拖垮 worker）——记 error 行、跳过。
    """
    from app.services.push_hub import emit as emit_svc

    if not receivers:
        receivers = ["SELF"]
    app = await get_or_create_stock_app(db)
    await get_or_create_stock_topic(db, app)
    results: list[dict] = []
    for receiver in receivers[:10]:  # PushPlus 实名好友消息单次 ≤10 人口径，全局也守这个数
        try:
            msg_id = await emit_svc.emit(
                db, app_id=app.app_id, topic_code=STOCK_TOPIC_CODE,
                receiver=receiver, title=title, body=content,
                channel_types=["wechat_oa"],
            )
            results.append({"receiver": receiver, "msg_id": msg_id, "status": "dispatched"})
        except Exception as e:  # noqa: BLE001
            logger.error("预警推送 emit 失败 receiver=%.6s…：%s", receiver, e)
            results.append({"receiver": receiver, "msg_id": None, "status": f"error:{e}"[:120]})
    return results


async def resolve_receivers(
    db: AsyncSession,
    alert_tokens: Optional[list],
    push_default: Optional[dict],
) -> list[str]:
    """单条预警接收方两级解析（v1.4 §3.3）：alert 覆盖 > 全局默认 > SELF。"""
    if alert_tokens:
        return [t for t in alert_tokens if t]
    mode = (push_default or {}).get("mode")
    if mode == "friends" and (push_default or {}).get("tokens"):
        return [t for t in push_default["tokens"] if t]
    return ["SELF"]
