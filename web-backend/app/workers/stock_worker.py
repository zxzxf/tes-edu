"""盯盘行情 worker：定时拉报价 → Redis 快照 → 预警触达评估。

随主进程 lifespan 启动（STOCK_WORKER_ENABLED=true 时），不新建容器
（137 内存红线，同推送 worker 口径）；亦可独立跑：
    python -m app.workers.stock_worker

循环节奏：每 stock_worker_interval（默认 15s）一轮——新浪批量接口一次请求
拿全部 symbol（自用远够"每秒>10次封IP"红线）。非交易时段（周末/收盘）不空转
拉源：Redis 快照 TTL 10 分钟自然过期，读路径发现 miss 会现拉兜底，看板显示
最后 fetched_at 时间戳（降级语义，v1.4 §七）。

铁律照抄（计划书 §五）：
- 数据源异常绝不阻断循环（整轮 try，下轮重来）；
- Redis 挂=跳过快照写，读路径直查源（cache 层已静默降级）；
- 单轮里触达判定/推送异常不 kill worker（fire_alerts 内部逐条兜）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.services.stock import cache as stock_cache
from app.services.stock import engine, provider

logger = logging.getLogger("stock_worker")


def _trading_now(cn_tz_min: int) -> bool:
    """A 股交易时段粗判（北京时间 周一至周五 9:15–11:30 / 13:00–15:05）。

    9:15 提前开盘前撮合预热、15:05 收盘缓冲；分钟级精度足够自用。
    """
    now = datetime.now(timezone.utc) + timedelta(minutes=cn_tz_min)
    if now.weekday() > 4:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60 + 5)


async def collect_symbols(db) -> list[str]:
    """本轮要跟踪的全部 symbol：固定指数 ∪ 自选股 ∪ 启用预警。"""
    from sqlalchemy import select

    from app.models.stock_models import StockAlert, StockWatchlist

    syms = list(provider.INDEX_SYMBOLS)
    syms += list((await db.execute(select(StockWatchlist.symbol))).scalars().all())
    syms += list((await db.execute(
        select(StockAlert.symbol).where(StockAlert.enabled.is_(True))
    )).scalars().all())
    # 去重保序
    seen, out = set(), []
    for s in syms:
        n = provider.normalize_symbol(s)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


async def tick_once(now_fn=_trading_now) -> dict:
    """单轮：拉源→写快照→触达评估。返回摘要（测试注入 now_fn 绕过时段闸门）。"""
    # 运行时动态取 SessionLocal：模块级 from-import 会把工厂固化在
    # 首次导入时刻（测试间 fixture 换库时会绑到别人的引擎——全量跑实测踩坑）
    from app.core.db import SessionLocal

    in_session = now_fn(480)
    quotes: dict = {}
    async with SessionLocal() as db:
        symbols = await collect_symbols(db)
        if symbols and (in_session or settings.stock_worker_fetch_off_hours):
            try:
                quotes = await provider.fetch_quotes(symbols)
            except Exception as e:  # noqa: BLE001
                logger.warning("行情拉取整轮失败（下轮重来）：%s", e)
            for sym, q in quotes.items():
                await stock_cache.put_quote(sym, q)
            if quotes:
                import time as _t
                await stock_cache.set_refresh_mark(_t.time())
        fired = await engine.fire_alerts(db, quotes)
    return {"symbols": len(symbols), "quotes": len(quotes), "fired": fired,
            "off_hours_skipped": not in_session}


async def run() -> None:
    interval = max(5, int(settings.stock_worker_interval))
    logger.info("stock worker 启动（每 %ss 一轮）", interval)
    while True:
        try:
            r = await tick_once()
            if r["fired"]:
                logger.info("本轮触发 %d 条预警", len(r["fired"]))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("stock worker 轮次异常（不中断）")
        await asyncio.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
