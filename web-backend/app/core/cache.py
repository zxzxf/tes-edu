"""可选 Redis 读缓存（web-backend 侧）。

设计红线：
- **缓存是加速层，不是数据源**。``REDIS_URL`` 未配置、Redis 宕机、超时、
  反序列化失败，一律静默降级为「没命中」，让调用方照常查 DB；任何异常都不得
  把学生端的读请求变成 500。
- **租户隔离写进 key**：同一个 Redis 被网关/agent/web 共用，试卷和判分结果属于
  学校资产，key 里不带 tenant_id 会跨校串数据。
- 全局复用一个连接池；``from_url`` 是惰性的，首次命令失败也只是降级，不阻断启动。

单测可替换模块级 ``_client`` 为内存假客户端（见 tests/test_exam_cache.py）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.config import settings

log = logging.getLogger(__name__)

_client: Any = None
_tried = False


def _get_client():
    global _client, _tried
    if _client is not None or _tried:
        return _client
    _tried = True
    if not settings.redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
    except Exception as exc:  # noqa: BLE001  依赖缺失/URL 非法都不该拖垮服务
        log.warning("redis client init skipped: %s", exc)
        _client = None
    return _client


async def get_json(key: str) -> Optional[Any]:
    c = _get_client()
    if c is None:
        return None
    try:
        raw = await c.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache get failed key=%s: %s", key, exc)
        return None


async def set_json(key: str, value: Any, ttl: int) -> None:
    c = _get_client()
    if c is None:
        return
    try:
        await c.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=max(1, ttl))
    except Exception as exc:  # noqa: BLE001
        log.debug("cache set failed key=%s: %s", key, exc)


async def delete(*keys: str) -> None:
    c = _get_client()
    if c is None or not keys:
        return
    try:
        await c.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache delete failed: %s", exc)


async def delete_prefix(prefix: str) -> None:
    """按前缀批量失效（人工改判/卷子重新发布时用）。

    用 SCAN 而不是 KEYS：Redis 里还有网关限流、LLM 缓存等大量 key，
    KEYS 会阻塞整个实例。生产 key 量不大，单批 200 个足够。
    """
    c = _get_client()
    if c is None:
        return
    try:
        batch: list[str] = []
        async for k in c.scan_iter(match=f"{prefix}*", count=200):
            batch.append(k)
            if len(batch) >= 200:
                await c.delete(*batch)
                batch.clear()
        if batch:
            await c.delete(*batch)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache delete_prefix failed prefix=%s: %s", prefix, exc)


async def ping() -> bool:
    c = _get_client()
    if c is None:
        return False
    try:
        return bool(await c.ping())
    except Exception:  # noqa: BLE001
        return False
