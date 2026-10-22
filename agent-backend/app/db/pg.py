"""asyncpg 连接池（真实模式连 106 的 pgvector）。

仅在 db_ready() 时被导入使用，dev 模式不加载。
"""
from __future__ import annotations

import asyncpg

from app.core import config

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=config.DB_DSN, min_size=1, max_size=4)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
