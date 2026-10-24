"""T-D1 请求级缓存与去重：同题 / 近似题命中缓存，杜绝重复烧 LLM。

缓存键：``subject + mode + hint_level + (content_hash 或问题文本归一化哈希)``。
``hint_level`` 必入键——同题不同 Hint 级不得混用缓存（验收硬要求）。

近似命中：对查询问题做 embedding，与 ``llm_cache`` 中已存向量做余弦相似度，
阈值 ``>= 0.98`` 视为近似同题，复用其 LLM 结果（复用 T-A3 的 embedding 通道）。

存储分层（全部 best-effort，存储抖动不得拖垮主链路）：
- ``Redis``（137 ``edu-redis``，agent 容器同网）TTL 7 天，主缓存；
- ``PG`` 表 ``llm_cache`` 持久化兜底（防 Redis 清空），并承载近似命中所需的向量检索；
- 二者皆不可用 → 进程内内存缓存（dev / 失控环境）。

落点设计：``cached_solve(remote_fn, question, context, mode, ...)`` 是通用包装，
``remote_fn`` 签名与 ``llm._remote_solve`` 对齐 ``(question, context, mode) -> dict``；
``llm.solve`` 与 ``socratic._remote_hint`` 各自把真实 LLM 调用包一层即可两路受益。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from typing import Awaitable, Callable, Optional

from app.core import config, embeddings

log = logging.getLogger("llm_cache")

CACHE_TTL_SECONDS = 7 * 24 * 3600
APPROX_THRESHOLD = 0.98
_PREFIX = "llm:v1:"


# --------------------------------------------------------------------- 缓存键

def cache_key(
    *,
    subject: Optional[str],
    mode: str,
    hint_level: Optional[int],
    question: str,
    content_hash: Optional[str] = None,
    variant: Optional[str] = None,
) -> str:
    """确定性缓存键。``hint_level`` 必入键——同题不同 Hint 级不得混用缓存。

    ``variant`` 用于把"同题同级但上下文不同"的情况分开，例如 Hint 的对话历史与
    当前步骤（``history``/``step``）——它们会实质改变 LLM 输出，混键会造成串味。
    """
    norm = re.sub(r"\s+", " ", (question or "").strip()).lower()
    if content_hash:
        base = f"ch:{content_hash}"
    else:
        base = "q:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    if variant:
        base += ":v" + hashlib.sha256(
            re.sub(r"\s+", " ", variant).encode("utf-8")
        ).hexdigest()[:8]
    hl = "x" if hint_level is None else str(int(hint_level))
    return f"{_PREFIX}{subject or 'na'}:{mode or 'explain'}:{hl}:{base}"


def cache_namespace(
    *,
    subject: Optional[str],
    mode: str,
    hint_level: Optional[int],
) -> tuple:
    """"命名空间"三元组，用于近似命中的隔离。

    **近似命中必须限定在同命名空间内**——否则 L1 的 Hint 会被 L2 复用（跨级串味），
    或解题结果被引导档命中。仅问题文本相似不足以复用 LLM 输出。
    """
    return (subject or None, mode or "explain", None if hint_level is None else int(hint_level))


# ------------------------------------------------------------------- 相似度工具

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# ------------------------------------------------------------- 存储后端（可插拔）

class MemoryStore:
    """进程内存储：dev / 测试 / 存储全不可用兜底。支持精确 + 近似命中。"""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self._data: dict[str, tuple[float, dict]] = {}
        # key -> (namespace 三元组, 向量)：近似命中必须限定在同命名空间内
        self._vec: dict[str, tuple[tuple, list[float]]] = {}
        self.ttl = ttl

    async def get(self, key: str) -> Optional[dict]:
        hit = self._data.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.time() - ts > self.ttl:
            self._data.pop(key, None)
            self._vec.pop(key, None)
            return None
        return val

    async def set(self, key, value, *, ttl=None, embedding=None, subject=None, mode=None, hint_level=None, **kw) -> None:
        self._data[key] = (time.time(), value)
        if embedding:
            self._vec[key] = (
                cache_namespace(subject=subject, mode=mode, hint_level=hint_level),
                list(embedding),
            )

    async def approx_get(
        self,
        embedding,
        threshold: float = APPROX_THRESHOLD,
        *,
        subject=None,
        mode=None,
        hint_level=None,
    ) -> Optional[dict]:
        if not embedding:
            return None
        ns = cache_namespace(subject=subject, mode=mode, hint_level=hint_level)
        now = time.time()
        best_key, best_score = None, -1.0
        for k, (kns, vec) in self._vec.items():
            if kns != ns:  # 跨命名空间（含跨 Hint 级）不复用
                continue
            hit = self._data.get(k)
            if not hit or now - hit[0] > self.ttl:
                continue
            s = _cosine(embedding, vec)
            if s > best_score:
                best_score, best_key = s, k
        if best_key and best_score >= threshold:
            return self._data[best_key][1]
        return None


class RedisStore:
    """Redis 主缓存（TTL 7 天）。Redis OSS 无向量检索，近似命中走 PG。"""

    def __init__(self, url: str):
        import redis.asyncio as aioredis

        self._r = aioredis.from_url(url, socket_connect_timeout=2, socket_timeout=2)

    async def get(self, key: str) -> Optional[dict]:
        try:
            raw = await self._r.get(key)
        except Exception as e:  # noqa: BLE001 存储抖动不得拖垮主链路
            log.warning("redis get failed: %s", e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def set(self, key, value, *, ttl: int = CACHE_TTL_SECONDS, embedding=None, **kw) -> None:
        try:
            await self._r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception as e:  # noqa: BLE001
            log.warning("redis set failed: %s", e)

    async def approx_get(self, embedding, threshold: float = APPROX_THRESHOLD, **kw) -> Optional[dict]:
        return None  # Redis OSS 不支持向量检索


class PgStore:
    """PG ``llm_cache`` 持久化兜底：承载精确命中 + 向量近似命中。"""

    async def get(self, key: str) -> Optional[dict]:
        from app.db.pg import get_pool

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT result_json FROM llm_cache "
                    "WHERE key=$1 AND (expires_at IS NULL OR expires_at > now())",
                    key,
                )
                if not row:
                    return None
                v = row["result_json"]
                return json.loads(v) if isinstance(v, str) else v
        except Exception as e:  # noqa: BLE001
            log.warning("pg cache get failed: %s", e)
            return None

    async def set(
        self,
        key,
        value,
        *,
        ttl: int = CACHE_TTL_SECONDS,
        embedding=None,
        question: str = "",
        mode: str = "",
        subject: str = "",
        hint_level=None,
        **kw,
    ) -> None:
        from app.db.pg import get_pool

        emb = "[" + ",".join(f"{x:.8g}" for x in embedding) + "]" if embedding else None
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO llm_cache(key,question,mode,subject,hint_level,result_json,embedding,expires_at)
                       VALUES($1,$2,$3,$4,$5,$6::jsonb,$7::vector, now()+interval '7 days')
                       ON CONFLICT(key) DO UPDATE SET
                         result_json=EXCLUDED.result_json, embedding=EXCLUDED.embedding,
                         created_at=now(), expires_at=EXCLUDED.expires_at""",
                    key,
                    question,
                    mode,
                    subject,
                    hint_level,
                    json.dumps(value, ensure_ascii=False),
                    emb,
                )
        except Exception as e:  # noqa: BLE001
            log.warning("pg cache set failed: %s", e)

    async def approx_get(
        self,
        embedding,
        threshold: float = APPROX_THRESHOLD,
        *,
        subject=None,
        mode=None,
        hint_level=None,
    ) -> Optional[dict]:
        """向量近似命中。**必须限定命名空间**（subject/mode/hint_level 三元组），
        否则会跨 Hint 级 / 跨档位复用 LLM 输出，造成串味。"""
        if not embedding:
            return None
        from app.db.pg import get_pool

        emb = "[" + ",".join(f"{x:.8g}" for x in embedding) + "]"
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT result_json FROM llm_cache
                       WHERE embedding IS NOT NULL
                         AND 1 - (embedding <=> $1::vector) >= $2
                         AND subject IS NOT DISTINCT FROM $3
                         AND mode IS NOT DISTINCT FROM $4
                         AND hint_level IS NOT DISTINCT FROM $5
                       ORDER BY embedding <=> $1::vector LIMIT 1""",
                    emb,
                    threshold,
                    subject,
                    mode or "explain",
                    hint_level,
                )
                if not row:
                    return None
                v = row["result_json"]
                return json.loads(v) if isinstance(v, str) else v
        except Exception as e:  # noqa: BLE001
            log.warning("pg cache approx failed: %s", e)
            return None


class CompositeStore:
    """主 + 兜底：精确双写；读取先主后兜底；近似检索走 PG（向量）。"""

    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    async def get(self, key: str) -> Optional[dict]:
        v = await self.primary.get(key)
        if v is not None:
            return v
        return await self.fallback.get(key)

    async def set(self, key, value, **kw) -> None:
        await self.primary.set(key, value, **kw)
        try:
            await self.fallback.set(key, value, **kw)
        except Exception:  # noqa: BLE001 兜底写失败不阻塞
            pass

    async def approx_get(self, embedding, threshold: float = APPROX_THRESHOLD, **kw) -> Optional[dict]:
        return await self.fallback.approx_get(embedding, threshold, **kw)


_DEFAULT_STORE: object = None


def get_default_store():
    """按运行环境挑选存储：Redis+PG → 组合；否则单项；皆无 → 进程内内存。"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is not None:
        return _DEFAULT_STORE
    redis_store = None
    pg_store = None
    if config.REDIS_URL:
        try:
            redis_store = RedisStore(config.REDIS_URL)
        except Exception as e:  # noqa: BLE001
            log.warning("redis store init failed: %s", e)
    if config.db_ready():
        try:
            pg_store = PgStore()
        except Exception as e:  # noqa: BLE001
            log.warning("pg store init failed: %s", e)
    if redis_store and pg_store:
        _DEFAULT_STORE = CompositeStore(redis_store, pg_store)
    elif redis_store:
        _DEFAULT_STORE = redis_store
    elif pg_store:
        _DEFAULT_STORE = pg_store
    else:
        _DEFAULT_STORE = MemoryStore()
    return _DEFAULT_STORE


# ---------------------------------------------------------------- 核心包装

async def cached_solve(
    remote_fn: Callable[..., Awaitable[dict]],
    question: str,
    context: dict | None,
    mode: str,
    *,
    subject: Optional[str] = None,
    hint_level: Optional[int] = None,
    content_hash: Optional[str] = None,
    variant: Optional[str] = None,
    store=None,
    on_hit: Callable[..., None] | None = None,
) -> dict:
    """带缓存的远程 LLM 调用包装。命中（精确或近似）则跳过 remote_fn，不烧 LLM。

    ``remote_fn`` 失败时返回 ``None``（如 Hint 生成异常回落模板）——此类结果不缓存，
    避免把"失败"固化进缓存。

    ``variant``：上下文变体签名（如 Hint 的 history/step）。**非空时只做精确命中**
    ——上下文不同的近似题复用会导致答非所问。

    ``on_hit``：命中回调（T-D3 用量统计用）——缓存命中不烧 LLM，但需要计数
    才能算出"缓存省了多少"。回调异常被吞掉，绝不影响主链路。
    """
    if store is None:
        # 总开关关闭（CACHE_ENABLED=false / 单测默认）→ 不接存储，直接回源，
        # 行为与接入缓存前完全一致（降级开关，也保证单测不碰 Redis/PG）。
        if not config.cache_enabled():
            return await remote_fn(question, context, mode)
        store = get_default_store()

    key = cache_key(
        subject=subject, mode=mode, hint_level=hint_level,
        question=question, content_hash=content_hash, variant=variant,
    )

    def _notify_hit() -> None:
        if on_hit is None:
            return
        try:
            on_hit()
        except Exception as e:  # noqa: BLE001 用量回调不得影响主链路
            log.debug("cache on_hit callback failed: %s", e)

    hit = await store.get(key)
    if hit is not None:
        log.debug("cache HIT %s", key)
        _notify_hit()
        return hit

    # 近似命中：对查询问题做 embedding，复用同命名空间下的近似同题结果
    emb = None
    if config.embed_ready() and not variant:
        try:
            emb = (await embeddings.embed([question]))[0]
            approx = await store.approx_get(
                emb, subject=subject, mode=mode, hint_level=hint_level,
            )
            if approx is not None:
                log.debug("cache APPROX HIT %s", key)
                # 顺手把近似结果以精确键落库，下次精确命中
                await store.set(
                    key, approx, embedding=emb,
                    question=question, mode=mode, subject=subject, hint_level=hint_level,
                )
                _notify_hit()
                return approx
        except Exception as e:  # noqa: BLE001 embedding 失败不得影响主链路
            log.warning("approx lookup failed: %s", e)

    # 未命中 → 调 LLM
    result = await remote_fn(question, context, mode)
    if result is not None:  # 失败结果（如 Hint None）不缓存
        try:
            await store.set(
                key, result, embedding=emb,
                question=question, mode=mode, subject=subject, hint_level=hint_level,
            )
        except Exception as e:  # noqa: BLE001 存储抖动不得拖垮主链路
            log.warning("cache set failed: %s", e)
    return result
