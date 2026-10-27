"""W6 BYOM T-M2：运行态模型配置解析 + SSRF 防护 + 缓存。

核心 API
--------
``resolve_llm_config(tenant_id)`` 在 agent 运行态按租户解析最终生效的 LLM 配置：
1. 命中 ``tenant_model_config`` 且 ``enabled=true`` → 解密 key 返回（租户级覆盖）；
2. 未命中 / 禁用 / 触发 SSRF 闸门 → **回落全局 env** （``config.LLM_*`` / ``EMBED_*``），
   行为与接入 BYOM 前完全一致；
3. 命中结果按 ``tenant_id`` 短 TTL 缓存（本地 SQLite 文件，无则进程内兜底），
   配置变更短窗口自愈。**缓存值刻意剔除 API Key**（方案 A），密钥每次从 DB 解密回填。

返回 ``LLMConfig`` 的字段对齐现有 socratic / judge / embeddings 调用契约
（base_url / api_key / model 等），后续可直接把租户配置接进真实调用链。

安全闸门（SSRF，必须随 T-M2 落地）
-------------------------------
``assert_safe_base_url``：仅 https；host 不得为 localhost / 字面量私网 IP /
云元数据 ``169.254.169.254``；可选域名后缀白名单（``BYOM_ALLOWED_DOMAINS``）。
解析时若租户 URL 不合规，直接回落全局并告警，绝不拿可疑地址去打 LLM。
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

from app.core import config
from app.core.vendor_crypto import decrypt

log = logging.getLogger("tenant_llm")

CACHE_PREFIX = "byom:cfg:"
DEFAULT_TTL = 120  # 秒；短 TTL 保证配置变更短窗口内自愈


class SSRFRejected(Exception):
    """base_url 触发 SSRF 闸门。"""


# ---------------------------------------------------------------- 解析结果结构

@dataclass
class LLMConfig:
    chat_base_url: str
    chat_api_key: str
    chat_model: str
    embed_base_url: str
    embed_api_key: str
    embed_model: str
    embed_dim: int
    model_simple: str
    model_hard: str
    rag_top_k: int
    source: str            # "tenant" | "global"
    provider: str = "custom"
    tenant_id: Optional[str] = None
    # 视觉模型段（拍照识题，T-VLM）：密钥复用 chat_api_key，不另存
    vlm_base_url: str = ""
    vlm_model: str = ""


# ------------------------------------------------------------------- SSRF 闸门

def _ttl() -> int:
    try:
        return int(os.getenv("BYOM_CONFIG_TTL", str(DEFAULT_TTL)))
    except ValueError:
        return DEFAULT_TTL


def _allowed_suffixes() -> list[str]:
    raw = os.getenv("BYOM_ALLOWED_DOMAINS", "")
    out: list[str] = []
    for s in raw.split(","):
        s = s.strip().lower().lstrip("*.").strip(".")
        if s:
            out.append(s)
    return out


def _is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def assert_safe_base_url(url: str) -> str:
    """SSRF 闸门：返回规范化 https URL；不合规抛 :class:`SSRFRejected`。"""
    if not url or not isinstance(url, str):
        raise SSRFRejected("base_url 为空或非法")
    try:
        parts = urlsplit(url.strip())
    except Exception as e:  # noqa: BLE001
        raise SSRFRejected(f"无法解析 base_url: {url!r}") from e
    scheme = (parts.scheme or "").lower()
    if scheme != "https":
        raise SSRFRejected(f"base_url 仅允许 https，拒绝 {scheme}:// —— {url}")
    host = (parts.hostname or "").lower()
    if not host:
        raise SSRFRejected(f"base_url 缺少 host: {url}")
    if host in ("localhost", "0.0.0.0", "::1", "[::]") or host.endswith(".local"):
        raise SSRFRejected(f"base_url 命中本地主机: {host}")
    if _is_private_ip(host) or host == "169.254.169.254":
        raise SSRFRejected(f"base_url 命中私网/云元数据地址: {host}")
    suffixes = _allowed_suffixes()
    if suffixes and not any(host == s or host.endswith("." + s) for s in suffixes):
        raise SSRFRejected(f"base_url 不在允许域名白名单: {host}")
    return url


# ------------------------------------------------------------------- 缓存后端

class _LocalCache:
    """进程内缓存（无 Redis 兜底 / 单测）。TTL 过期自动失效。"""

    def __init__(self, ttl: int = DEFAULT_TTL):
        self._d: dict[str, tuple[float, dict]] = {}
        self.ttl = ttl

    async def get(self, key: str) -> Optional[dict]:
        hit = self._d.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.time() - ts > self.ttl:
            self._d.pop(key, None)
            return None
        return val

    async def set(self, key: str, value: dict) -> None:
        self._d[key] = (time.time(), value)

    async def delete(self, key: str) -> None:
        self._d.pop(key, None)


class _SqliteCache:
    """本地 SQLite 缓存：把解析后的租户模型配置落本地文件，查询走本地磁盘（亚毫秒级），

    不再每次请求都打 Redis / Postgres，提升模型配置查询性能（T-VLM 后续需求：
    所有面向学生端的模型变量都从学校 BYOM 配置取，配置查询必须够快）。

    - 单文件 + WAL + busy_timeout：轻量并发可读，写入冲突自动退避；
    - 惰性建表 ``byom_cfg_cache(key, value, ts)``，过期由 TTL 在 get 时惰性清理；
    - 打开/读写失败一律回落调用方（最终回落进程内缓存），绝不拖垮主链路；
    - 用标准库 sqlite3 + 线程池（非 aiosqlite）：不与 event loop 绑定，
      loop 关闭 / 进程重载时不会挂住收尾。
    """

    def __init__(self, db_path: str, ttl: int = DEFAULT_TTL):
        self.db_path = db_path
        self.ttl = ttl
        self._conn = None
        self._lock = asyncio.Lock()

    def _connect(self):
        """同步建连（在 worker 线程内执行）。

        用标准库 ``sqlite3`` 而非 aiosqlite：后者维护常驻后台线程，event loop
        关闭时（测试收尾 / 进程重载）清理不掉会挂住解释器。这里把同步调用丢进
        线程池，既保持 async 接口，又不与任何 event loop 绑定。
        """
        if self._conn is None:
            d = os.path.dirname(self.db_path)
            if d:
                os.makedirs(d, exist_ok=True)
            # check_same_thread=False：连接由线程池里的任意 worker 复用
            conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS byom_cfg_cache ("
                "key TEXT PRIMARY KEY, value TEXT, ts REAL)"
            )
            conn.commit()
            self._conn = conn
        return self._conn

    async def _op(self, fn):
        """在 worker 线程里执行一次 DB 操作，串行化以避免写冲突。"""
        async with self._lock:

            def _go():
                return fn(self._connect())

            return await asyncio.to_thread(_go)

    async def get(self, key: str) -> Optional[dict]:
        def _q(conn):
            cur = conn.execute(
                "SELECT value, ts FROM byom_cfg_cache WHERE key=?", (key,)
            )
            return cur.fetchone()

        try:
            row = await self._op(_q)
        except Exception as e:  # noqa: BLE001
            log.warning("byom sqlite get failed: %s", e)
            return None
        if not row:
            return None
        val, ts = row
        if time.time() - ts > self.ttl:
            await self.delete(key)
            return None
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return None

    async def set(self, key: str, value: dict) -> None:
        # 序列化在主线程完成，避免把不可序列化对象带进 worker 线程才炸
        try:
            payload = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as e:  # noqa: BLE001
            log.warning("byom sqlite set skipped (unserializable): %s", e)
            return

        def _w(conn):
            conn.execute(
                "INSERT INTO byom_cfg_cache(key, value, ts) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (key, payload, time.time()),
            )
            conn.commit()

        try:
            await self._op(_w)
        except Exception as e:  # noqa: BLE001
            log.warning("byom sqlite set failed: %s", e)

    async def delete(self, key: str) -> None:
        def _d(conn):
            conn.execute("DELETE FROM byom_cfg_cache WHERE key=?", (key,))
            conn.commit()

        try:
            await self._op(_d)
        except Exception as e:  # noqa: BLE001
            log.warning("byom sqlite delete failed: %s", e)

    async def close(self) -> None:
        """释放底层连接（进程退出 / 测试收尾用）。"""
        conn = self._conn
        self._conn = None
        if conn is not None:
            try:
                await asyncio.to_thread(conn.close)
            except Exception:  # noqa: BLE001
                pass


_cache_backend: object = None


def get_cache():
    """惰性挑选缓存后端：本地 SQLite 文件（BYOM_CACHE_DB，默认 /app/data/byom_cache.db）

    为首选；打开失败则回落进程内缓存。配置解析结果走本地磁盘，查询远低于 Redis /
    Postgres 网络往返，契合「所有面向学生端的模型变量都从学校 BYOM 配置取」的高频查询。
    """
    global _cache_backend
    if _cache_backend is not None:
        return _cache_backend
    db = os.getenv("BYOM_CACHE_DB") or "/app/data/byom_cache.db"
    try:
        _cache_backend = _SqliteCache(db, _ttl())
    except Exception as e:  # noqa: BLE001
        log.warning("byom sqlite init failed, fall back to local: %s", e)
        _cache_backend = _LocalCache(_ttl())
    return _cache_backend


async def invalidate_tenant(tenant_id: str, cache: object | None = None) -> None:
    """配置变更后主动清缓存（T-M3 管理端保存时调用，便于即时生效）。"""
    backend = cache if cache is not None else get_cache()
    try:
        await backend.delete(CACHE_PREFIX + tenant_id)
    except Exception as e:  # noqa: BLE001
        log.debug("byom invalidate failed: %s", e)


# ------------------------------------------------------------------- 数据加载

async def _fetch_tenant_row(tenant_id: str) -> Optional[dict]:
    """默认加载器：从共享 106 postgres 的 ``tenant_model_config`` 取一行。

    ``get_pool``（asyncpg）在此惰性导入——未配置 DB_DSN 的单测/降级环境导入本模块
    时不会拖入 asyncpg；真实路径调用本函数才需要。
    """
    try:
        from app.db.pg import get_pool

        pool = await get_pool()
    except Exception as e:  # noqa: BLE001
        log.warning("byom db pool unavailable: %s", e)
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT tenant_id, provider, base_url, api_key_enc, model,
                          embed_base_url, embed_api_key_enc, embed_model, embed_dim,
                          model_simple, model_hard, enabled, rag_top_k,
                          vlm_base_url, vlm_model
                   FROM tenant_model_config WHERE tenant_id=$1""",
                tenant_id,
            )
        return dict(row) if row else None
    except Exception as e:  # noqa: BLE001
        log.warning("byom fetch failed: %s", e)
        return None


# ------------------------------------------------------------------- 解析主逻辑

def _global_config() -> LLMConfig:
    """回落全局 env（接入 BYOM 前的行为）。"""
    return LLMConfig(
        chat_base_url=config.LLM_BASE_URL,
        chat_api_key=config.LLM_API_KEY,
        chat_model=config.LLM_MODEL,
        embed_base_url=config.EMBED_BASE_URL,
        embed_api_key=config.EMBED_API_KEY,
        embed_model=config.EMBED_MODEL or "",
        embed_dim=config.EMBED_DIM,
        model_simple=config.LLM_MODEL_SIMPLE,
        model_hard=config.LLM_MODEL_HARD,
        rag_top_k=config.TOP_K,
        source="global",
        provider="global",
        # VLM 全局段（env）：VLM_BASE_URL 缺省回落 LLM_BASE_URL
        vlm_base_url=config.VLM_BASE_URL or "",
        vlm_model=config.VLM_MODEL or "",
    )


async def resolve_llm_config(
    tenant_id: Optional[str] = None,
    *,
    loader: Callable[[str], Awaitable[Optional[dict]]] | None = None,
    cache: object | None = None,
) -> LLMConfig:
    """按 ``tenant_id`` 运行态解析最终 LLM 配置。

    参数
    ----
    tenant_id: 租户标识；``None``/空 → 直接回落全局（C 端无租户场景）。
    loader: 注入自定义行加载器（测试用）；默认查 106 共享库。
    cache: 注入自定义缓存后端（测试用）；默认 Redis/进程内。

    约定
    ----
    - 全局回落结果**不缓存**（租户配置随时可能上线，需即时生效）；
    - 仅缓存有效租户配置；SSRF 不合规 / 解密失败一律回落全局并告警。
    """
    if not tenant_id:
        return _global_config()

    backend = cache if cache is not None else get_cache()
    key = CACHE_PREFIX + tenant_id
    try:
        cached = await backend.get(key)
    except Exception as e:  # noqa: BLE001
        log.debug("byom cache get failed: %s", e)
        cached = None
    if cached:
        # 缓存命中：非敏感字段已缓存，但 **API Key 不落盘**，需从 DB 重新解密回填。
        # 这是方案 A 的取舍——用一次查库+解密（~3ms）换「密钥绝不出现在本地文件里」。
        load = loader or _fetch_tenant_row
        row = await load(tenant_id)
        if row and row.get("enabled"):
            try:
                chat_key = decrypt(bytes(row["api_key_enc"]))
                cached["chat_api_key"] = chat_key
                # embed key：租户单独配了就解密，否则复用 chat key
                if row.get("embed_api_key_enc"):
                    try:
                        cached["embed_api_key"] = decrypt(bytes(row["embed_api_key_enc"]))
                    except Exception:  # noqa: BLE001
                        cached["embed_api_key"] = chat_key
                else:
                    cached["embed_api_key"] = chat_key
            except Exception as e:  # noqa: BLE001
                log.warning("byom cache key refill failed for %s: %s", tenant_id, e)
                row = None
        if not row or not row.get("enabled"):
            # 租户已被删除/禁用：缓存失效，清掉后回落全局。
            # 必须先删缓存，否则下次仍会命中这份「没有 key」的脏配置。
            try:
                await backend.delete(key)
            except Exception:  # noqa: BLE001
                pass
            return _global_config()
        # 缓存里存的 base_url 是上次校验过的，但缓存文件理论上可被篡改，
        # 这里按同样闸门复检一遍（纯字符串判定，成本可忽略）。
        for _u in (cached.get("chat_base_url"), cached.get("embed_base_url")):
            if _u:
                try:
                    assert_safe_base_url(_u)
                except SSRFRejected as e:
                    log.warning("byom SSRF guard blocked cached url for %s: %s", tenant_id, e)
                    return _global_config()
        return LLMConfig(**cached)

    load = loader or _fetch_tenant_row
    row = await load(tenant_id)
    if not row or not row.get("enabled"):
        return _global_config()

    # 解密 chat key
    try:
        chat_key = decrypt(bytes(row["api_key_enc"]))
    except Exception as e:  # noqa: BLE001
        log.warning("byom decrypt chat key failed for %s: %s", tenant_id, e)
        return _global_config()

    # SSRF 闸门（chat 段）
    try:
        assert_safe_base_url(row["base_url"])
    except SSRFRejected as e:
        log.warning("byom SSRF guard blocked chat base_url for %s: %s", tenant_id, e)
        return _global_config()

    # embedding 段（缺省复用 chat 段）
    embed_base = row.get("embed_base_url") or row["base_url"]
    try:
        assert_safe_base_url(embed_base)
    except SSRFRejected as e:
        log.warning("byom SSRF guard blocked embed base_url for %s: %s", tenant_id, e)
        return _global_config()
    if row.get("embed_api_key_enc"):
        try:
            embed_key = decrypt(bytes(row["embed_api_key_enc"]))
        except Exception:  # noqa: BLE001
            embed_key = chat_key
    else:
        embed_key = chat_key

    cfg = LLMConfig(
        chat_base_url=row["base_url"],
        chat_api_key=chat_key,
        chat_model=row["model"],
        embed_base_url=embed_base,
        embed_api_key=embed_key or chat_key,
        embed_model=row.get("embed_model") or "",
        embed_dim=int(row.get("embed_dim") or config.EMBED_DIM),
        model_simple=row.get("model_simple") or row["model"],
        model_hard=row.get("model_hard") or row["model"],
        rag_top_k=int(row.get("rag_top_k") or config.TOP_K),
        source="tenant",
        provider=row.get("provider") or "custom",
        tenant_id=tenant_id,
        # 视觉模型段（T-VLM）：vlm_base_url 缺省复用 chat base_url；密钥复用 chat key
        vlm_base_url=row.get("vlm_base_url") or row["base_url"],
        vlm_model=row.get("vlm_model") or "",
    )
    # 租户未单独配 vlm_model 时，回落平台全局 VLM（保留接入 BYOM 前的拍照识题能力）
    if not cfg.vlm_model and config.vlm_ready():
        cfg.vlm_base_url = config.VLM_BASE_URL or cfg.vlm_base_url
        cfg.vlm_model = config.VLM_MODEL or ""
    try:
        # 安全：缓存时剔除 API Key，避免密钥落盘；下次读取时从 DB 重新解密
        cache_val = {k: v for k, v in asdict(cfg).items() if "api_key" not in k}
        await backend.set(key, cache_val)
    except Exception as e:  # noqa: BLE001
        log.warning("byom cache set failed: %s", e)
    return cfg


# ------------------------------------------------------------------- 调用方便捷封装

async def resolve_for(
    tenant_id: Optional[str] = None,
    *,
    llm_cfg: "LLMConfig | None" = None,
) -> "LLMConfig":
    """统一解析入口：已持有 ``llm_cfg`` 直接复用（避免重复查库/缓存）；否则按 tenant_id 解析。

    供 llm / judge / intent / socratic 等调用点在拿到租户配置后向下游透传，
    保证一次请求内只解析一次。
    """
    if llm_cfg is not None:
        return llm_cfg
    return await resolve_llm_config(tenant_id)


def make_client(cfg: "LLMConfig"):
    """按解析结果构造 OpenAI 兼容客户端（chat 段）。"""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=cfg.chat_api_key, base_url=cfg.chat_base_url)


def vlm_enabled(cfg: "LLMConfig") -> bool:
    """VLM（拍照识题）是否可用：base_url + 密钥（复用 chat key）+ 视觉模型名三者齐备。"""
    return bool(cfg.vlm_base_url and cfg.chat_api_key and cfg.vlm_model)
