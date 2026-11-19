"""三级频控：token 每分钟 → 应用每分钟 → 主题每天。

实现=Redis INCR 滑窗（分钟窗 key 带 yyyymmddHHMM、日窗带 yyyymmdd）。
**保护性限流**：cache.incr 在 Redis 不可用时返回 0（放行）——频控挂了
宁可少拦，绝不反把发送链拦死（与缓存层"加速层不是数据源"同一口径）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core import cache


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def check_token_qpm(token_id: int, limit: int) -> bool:
    """True=未超限。"""
    if limit <= 0:
        return True
    k = _now().strftime("%Y%m%d%H%M")
    n = await cache.incr(f"push:rl:tok:{token_id}:{k}", ttl=120)
    return n <= limit


async def check_app_qpm(app_id: int, limit: int) -> bool:
    if limit <= 0:
        return True
    k = _now().strftime("%Y%m%d%H%M")
    n = await cache.incr(f"push:rl:app:{app_id}:{k}", ttl=120)
    return n <= limit


async def check_topic_daily(app_id: int, topic_code: str, limit: int) -> bool:
    if limit <= 0:
        return True
    k = _now().strftime("%Y%m%d")
    n = await cache.incr(f"push:rl:topic:{app_id}:{topic_code}:{k}", ttl=90000)
    return n <= limit
