"""预警触达引擎：报价 × 规则阈值比较 → 冷却去重 → 落触发记录 + 推送。

纯函数判定 + 异步副作用分离（judge 可单测、fire 打桩）。
冷却（v1.4 §七 推送限频）：同一 alert 在 cooldown_minutes 内只触发一次，
判定用 stock_alert_log 最近 fired_at，不引入额外状态——重启不复活已冷却项。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock_models import StockAlert, StockAlertLog, StockConfig

logger = logging.getLogger("stock.engine")

CONDITIONS = ("price_above", "price_below", "pct_up", "pct_down")


def judge(alert: StockAlert, quote: dict) -> bool:
    """单规则单报价触达判定（纯函数，单测锁）。价格缺失/零报价（停牌）不触发。"""
    price = quote.get("price") or 0
    if price <= 0:
        return False
    th = float(alert.threshold or 0)
    cond = alert.condition
    if cond == "price_above":
        return price >= th
    if cond == "price_below":
        return price > 0 and price <= th
    if cond == "pct_up":
        return (quote.get("pct") or 0) >= th
    if cond == "pct_down":
        return (quote.get("pct") or 0) <= -th
    return False


async def in_cooldown(db: AsyncSession, alert: StockAlert, now: datetime) -> bool:
    """冷却窗口内已触发过 → True（跳过本次）。"""
    if alert.cooldown_minutes <= 0:
        return False
    last = (
        await db.execute(
            select(StockAlertLog.fired_at)
            .where(StockAlertLog.alert_id == alert.alert_id)
            .order_by(StockAlertLog.fired_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last < timedelta(minutes=alert.cooldown_minutes)


async def get_push_default(db: AsyncSession) -> dict:
    row = (
        await db.execute(select(StockConfig).where(StockConfig.key == "push_default"))
    ).scalars().first()
    return (row.value or {}) if row else {}


def fmt_title(alert: StockAlert, quote: dict) -> str:
    sign = "+" if (quote.get("pct") or 0) >= 0 else ""
    return f"盯盘预警 {quote.get('name') or alert.symbol} {quote.get('price'):.2f}（{sign}{quote.get('pct'):.2f}%）"


#: 投资建议口径（纯规则文案，不构成实际投资意见；每条末尾统一附风险提示）
ADVICE = {
    "pct_down": "跌幅已达预警线。建议：先分辨是个股独立下跌还是大盘/板块联动——"
        "基本面没变可观察为主、不急于抄底；利空驱动的下跌警惕趋势转弱，严格止损纪律。",
    "pct_up": "涨幅已达预警线。建议：获利盘可考虑分批止盈；趋势延续则持有并设置回撤止盈线，防冲高回落。",
    "price_above": "价格上破目标位。建议：关注是否放量突破——缩量上破谨防假突破，回踩确认不破再跟进，避免追高。",
    "price_below": "价格下破目标位。建议：留意破位后支撑；若收盘价持续低于该位，趋势转弱概率大，持仓注意控制风险。",
}


def build_advice(alert: StockAlert, quote: dict) -> str:
    line = ADVICE.get(alert.condition)
    if not line:
        return ""
    return f"\n投资建议：{line}（仅供参考，不构成投资建议）"


def fmt_body(alert: StockAlert, quote: dict) -> str:
    """微信触达消息体：分行结构——当前价/时间/涨跌幅/涨跌额/参考位/触发条件/建议。

    推送中心 App 收件箱与 PushPlus 微信共用此文本；字段缺失（停牌等）留 '—'。
    """
    cond_cn = {
        "price_above": "价格上破", "price_below": "价格下破",
        "pct_up": "涨幅达到", "pct_down": "跌幅达到",
    }.get(alert.condition, alert.condition)
    th = float(alert.threshold or 0)
    unit = "%" if alert.condition in ("pct_up", "pct_down") else "元"
    pct = quote.get("pct") or 0
    change = quote.get("change") or 0
    now = datetime.now(timezone.utc).astimezone()
    name = quote.get("name") or ""
    price = quote.get("price") or 0
    lines = [
        f"{name}（{alert.symbol}）触发盯盘预警",
        f"当前价格：{price:.2f} 元　涨跌：{change:+.2f} 元（{pct:+.2f}%）",
        f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"昨收 {quote.get('prev_close') or '—'}　今开 {quote.get('open') or '—'}　"
        f"最高 {quote.get('high') or '—'}　最低 {quote.get('low') or '—'}",
        f"触发条件：{cond_cn} {th:g}{unit}",
    ]
    return "\n".join(lines) + build_advice(alert, quote)


async def fire_alerts(db: AsyncSession, quotes: dict[str, dict]) -> list[dict]:
    """一轮触达评估：对全部启用规则 × 快照报价判定 → 冷却过滤 → 落 log + 推送。

    返回本轮触发的 log 摘要列表（worker 日志与单测断言用）。
    推送失败不影响落库（站内提示兜底，v1.4 验收"推送失败不拖垮 worker"）。
    """
    from app.services.stock import push as push_svc

    alerts = (
        await db.execute(select(StockAlert).where(StockAlert.enabled.is_(True)))
    ).scalars().all()
    if not alerts:
        return []
    push_default = await get_push_default(db)
    now = datetime.now(timezone.utc)
    fired: list[dict] = []

    for alert in alerts:
        quote = quotes.get(alert.symbol)
        if not quote or not judge(alert, quote):
            continue
        if await in_cooldown(db, alert, now):
            logger.debug("预警 %s 冷却中，跳过", alert.alert_id)
            continue

        receivers: list[dict] = []
        pushed = False
        if alert.push_enabled:
            targets = await push_svc.resolve_receivers(db, alert.receiver_tokens, push_default)
            receivers = await push_svc.push_alert(
                db, fmt_title(alert, quote), fmt_body(alert, quote), targets
            )
            pushed = any(r.get("msg_id") for r in receivers)

        log = StockAlertLog(
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            price=quote.get("price") or 0,
            pct=quote.get("pct") or 0,
            fired_at=now,
            pushed=pushed,
            receivers=receivers,
        )
        db.add(log)
        await db.commit()
        fired.append({"alert_id": alert.alert_id, "symbol": alert.symbol, "pushed": pushed})
        logger.info("预警触发 alert=%s %s pushed=%s receivers=%d",
                    alert.alert_id, alert.symbol, pushed, len(receivers))
    return fired
