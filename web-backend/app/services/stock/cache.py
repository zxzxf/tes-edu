"""行情 Redis 快照层：worker 写、API 读；Redis 挂=直调源降级（cache.py 同款惯例）。

key：
- stock:quote:{symbol}   最新报价 JSON（TTL 10 分钟：盘中 worker 每 15s 刷，
  收盘/周末自然过期回源，读到即新鲜）
- stock:refresh          最近一轮刷新高（fetched_at 秒级时间戳，看板显示数据时刻）
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.core import cache

logger = logging.getLogger("stock.cache")

QUOTE_TTL = 600
REFRESH_KEY = "stock:refresh"


async def put_quote(symbol: str, quote: dict) -> None:
    await cache.set_json(f"stock:quote:{symbol}", quote, QUOTE_TTL)


async def get_quote(symbol: str) -> Optional[dict]:
    return await cache.get_json(f"stock:quote:{symbol}")


async def get_quotes(symbols: list[str]) -> dict[str, dict]:
    """批量读快照；miss 的不在返回里（调用方对缺失 symbol 现拉源补齐）。"""
    out: dict[str, dict] = {}
    for s in symbols:
        q = await get_quote(s)
        if q:
            out[s] = q
    return out


async def set_refresh_mark(ts: float) -> None:
    await cache.set_json(REFRESH_KEY, {"fetched_at": ts}, 86400)


async def get_refresh_mark() -> Optional[float]:
    d = await cache.get_json(REFRESH_KEY)
    return d.get("fetched_at") if isinstance(d, dict) else None
