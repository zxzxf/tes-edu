"""W6 BYOM T-M2：运行态模型配置解析 + SSRF 防护 + 缓存。

核心 API
--------
``resolve_llm_config(tenant_id)`` 在 agent 运行态按租户解析最终生效的 LLM 配置：
1. 命中 ``tenant_model_config`` 且 ``enabled=true`` → 解密 key 返回（租户级覆盖）；
2. 未命中 / 禁用 / 触发 SSRF 闸门 → **回落全局 env** （``config.LLM_*`` / ``EMBED_*``），
   行为与接入 BYOM 前完全一致；
3. 命中结果按 ``tenant_id`` 短 TTL 缓存（Redis，无则进程内兜底），配置变更短窗口自愈。

返回 ``LLMConfig`` 的字段对齐现有 socratic / judge / embeddings 调用契约
（base_url / api_key / model 等），后续可直接把租户配置接进真实调用链。

安全闸门（SSRF，必须随 T-M2 落地）
-------------------------------
``assert_safe_base_url``：仅 https；host 不得为 localhost / 字面量私网 IP /
云元数据 ``169.254.169.254``；可选域名后缀白名单（``BYOM_ALLOWED_DOMAINS``）。
解析时若租户 URL 不合规，直接回落全局并告警，绝不拿可疑地址去打 LLM。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
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


class _RedisCache:
    """Redis 缓存（与 llm_cache 同思路：存储抖动不拖垮主链路）。"""

    def __init__(self, url: str, ttl: int = DEFAULT_TTL):
        import redis.asyncio as aioredis

        self._r = aioredis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        self.ttl = ttl

    async def get(self, key: str) -> Optional[dict]:
        try:
            raw = await self._r.get(key)
        except Exception as e:  # noqa: BLE001
            log.warning("byom redis get failed: %s", e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def set(self, key: str, value: dict) -> None:
        try:
            await self._r.set(key, json.dumps(value, ensure_ascii=False), ex=self.ttl)
        except Exception as e:  # noqa: BLE001
            log.warning("byom redis set failed: %s", e)

    async def delete(self, key: str) -> None:
        try:
            await self._r.delete(key)
        except Exception as e:  # noqa: BLE001
            log.warning("byom redis delete failed: %s", e)


_cache_backend: object = None


def get_cache():
    """惰性挑选缓存后端：有 REDIS_URL → Redis；否则进程内。"""
    global _cache_backend
    if _cache_backend is not None:
        return _cache_backend
    if config.REDIS_URL:
        try:
            _cache_backend = _RedisCache(config.REDIS_URL, _ttl())
        except Exception as e:  # noqa: BLE001
            log.warning("byom redis init failed, fall back to local: %s", e)
    if _cache_backend is None:
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
        await backend.set(key, asdict(cfg))
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
