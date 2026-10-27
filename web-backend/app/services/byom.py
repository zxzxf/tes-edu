"""BYOM 租户级模型配置服务层（W6 T-M1 + T-M3）。

- 密钥加解密复用 app/services/vendor_crypto.py（AES-256-GCM，与厂商凭证同一主密钥
  VENDOR_CREDENTIAL_KEY），不在此重复实现加密原语；
- 明文段（api_key）只在后端内存中短暂存在，落库即密文、出响应即脱敏；
- 基础 CRUD 供 T-M3 后台 API 与 T-M2 解析共用；
- T-M3 新增：``serialize_config_masked``（脱敏序列化）、``probe_endpoint``
  （连通性自检）、``invalidate_agent_cache``（配置变更后清 agent 侧解析缓存）。
"""

import logging
import os
import time
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ssrf import SSRFRejected, assert_safe_base_url
from app.models.byom import TenantModelConfig
from app.services import vendor_crypto as vc

log = logging.getLogger(__name__)


# ---------- 密钥脱敏展示 ----------

def mask_key(plain: Optional[str]) -> str:
    """仅展示末 4 位：sk-****abcd → ****abcd；空值返回空串。"""
    if not plain:
        return ""
    if len(plain) <= 4:
        return plain
    return "****" + plain[-4:]


# ---------- 基础 CRUD（密钥以明文入参，落库前加密） ----------

async def get_config(db: AsyncSession, tenant_id: str) -> Optional[TenantModelConfig]:
    res = await db.execute(
        select(TenantModelConfig).where(TenantModelConfig.tenant_id == tenant_id)
    )
    return res.scalar_one_or_none()


async def list_configs(db: AsyncSession) -> List[TenantModelConfig]:
    res = await db.execute(select(TenantModelConfig))
    return list(res.scalars().all())


async def upsert_config(
    db: AsyncSession,
    *,
    tenant_id: str,
    provider: str,
    base_url: str,
    model: str,
    api_key_plain: Optional[str] = None,  # None/空 = 保持已存密钥不覆盖
    enabled: bool = True,
    embed_base_url: Optional[str] = None,
    embed_api_key_plain: Optional[str] = None,  # None = 保持已存；"" = 清空
    embed_model: Optional[str] = None,
    embed_dim: int = 1024,
    model_simple: Optional[str] = None,
    model_hard: Optional[str] = None,
    rag_top_k: int = 4,
) -> TenantModelConfig:
    """按 tenant_id 幂等 upsert。

    - api_key_plain 为 None 或空串 → 不重加密，保留原 api_key_enc（编辑态「留空保持不变」）；
    - embed_api_key_plain 为 None → 保留原 embed_api_key_enc；为空串 → 显式清空。
    """
    existing = await get_config(db, tenant_id)
    obj = existing or TenantModelConfig(tenant_id=tenant_id)

    obj.provider = provider
    obj.base_url = base_url
    obj.model = model
    if api_key_plain:
        obj.api_key_enc = vc.encrypt(api_key_plain)
    if embed_api_key_plain is not None:
        obj.embed_api_key_enc = vc.encrypt(embed_api_key_plain) if embed_api_key_plain else None
    obj.embed_base_url = embed_base_url
    obj.embed_model = embed_model
    obj.embed_dim = embed_dim
    obj.model_simple = model_simple
    obj.model_hard = model_hard
    obj.enabled = enabled
    obj.rag_top_k = rag_top_k

    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_config(db: AsyncSession, tenant_id: str) -> None:
    obj = await get_config(db, tenant_id)
    if obj is not None:
        await db.delete(obj)
        await db.commit()


# ---------- 解密取用（T-M2 解析时调用） ----------

def decrypt_api_key(blob) -> str:
    return vc.decrypt(blob)


def decrypt_embed_api_key(blob) -> Optional[str]:
    return vc.decrypt(blob) if blob else None


# ---------- T-M3：脱敏序列化（出响应） ----------

def serialize_config_masked(obj: TenantModelConfig) -> dict:
    """把 TenantModelConfig 行脱敏序列化（api_key 只显末 4 位，明文不回传）。

    注：明文回显走独立接口 ``POST /byom/config/reveal``（需 system:byom:manage，
    且留审计日志），本函数任何情况下都不带明文。
    """
    api_key_masked = ""
    if obj.api_key_enc:
        try:
            api_key_masked = mask_key(vc.decrypt(obj.api_key_enc))
        except Exception:  # noqa: BLE001 密钥轮换/密文损坏：不吐明文，只给空
            api_key_masked = ""
    embed_masked = ""
    if obj.embed_api_key_enc:
        try:
            embed_masked = mask_key(vc.decrypt(obj.embed_api_key_enc))
        except Exception:  # noqa: BLE001
            embed_masked = ""
    return {
        "tenant_id": obj.tenant_id,
        "provider": obj.provider,
        "base_url": obj.base_url,
        "model": obj.model,
        "embed_base_url": obj.embed_base_url or "",
        "embed_model": obj.embed_model or "",
        "embed_dim": obj.embed_dim,
        "model_simple": obj.model_simple or "",
        "model_hard": obj.model_hard or "",
        "rag_top_k": obj.rag_top_k,
        "enabled": obj.enabled,
        "has_api_key": bool(obj.api_key_enc),
        "api_key_masked": api_key_masked,
        "has_embed_api_key": bool(obj.embed_api_key_enc),
        "embed_api_key_masked": embed_masked,
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
    }


# ---------- T-M5：明文回显（列表页「反显」按钮，需权限 + 审计） ----------

def reveal_keys(obj: TenantModelConfig) -> dict:
    """解密取出明文密钥（仅供后台显式「反显」操作调用）。

    - 解密失败（主密钥轮换 / 密文损坏）抛 ValueError，由 API 层转 400，
      绝不返回半截明文；
    - 调用方必须已过 ``require_perm("system:byom:manage")`` 并记录审计日志。
    """
    api_key = ""
    if obj.api_key_enc:
        api_key = vc.decrypt(obj.api_key_enc)
    embed_key = ""
    if obj.embed_api_key_enc:
        embed_key = vc.decrypt(obj.embed_api_key_enc)
    return {"api_key": api_key, "embed_api_key": embed_key}


# ---------- T-M3：连通性自检 ----------

async def probe_endpoint(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 8.0,
    _transport=None,
) -> dict:
    """连通性自检：SSRF 校验后实调 OpenAI 兼容 /chat/completions（极小请求）。

    - 不合规 URL → ok=false + SSRF 说明（不 500）；
    - ``_transport`` 可注入（单测用），默认走真实 httpx；
    - 返回 {ok, status_code, latency_ms, detail}；网络错误也是 ok=false 不 500。
    """
    try:
        assert_safe_base_url(base_url)
    except SSRFRejected as e:
        return {"ok": False, "status_code": None, "latency_ms": 0, "detail": f"SSRF 拦截：{e}"}

    transport = _transport or _httpx_transport
    started = time.perf_counter()
    try:
        resp = await transport(base_url, api_key, model, timeout)
        latency = int((time.perf_counter() - started) * 1000)
        if hasattr(resp, "status_code"):
            sc = resp.status_code
            detail = f"HTTP {sc}"
            if sc == 401:
                detail = "密钥无效或缺失 (401)"
            elif sc == 403:
                detail = "无权限访问 (403)，检查密钥或账号额度"
            elif sc == 404:
                detail = "端点路径不存在 (404)，请检查 base_url 是否含 /v1"
            elif sc == 429:
                # 429 = 地址与密钥都有效，只是速率/配额受限：文案要写清，别误报成不可达
                detail = "被限流 (429)：地址与密钥有效，仅速率/配额受限，稍后重试或提配额"
            return {"ok": sc < 400, "status_code": sc, "latency_ms": latency, "detail": detail}
        # 注入的假响应可能直接返回 dict
        return resp
    except Exception as e:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": latency,
            "detail": f"{type(e).__name__}: {str(e)[:200]}",
        }


# ---------- T-M5：Embedding 段连通性自检（与 Chat 段分开探测） ----------

async def probe_embedding(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 8.0,
    _transport=None,
) -> dict:
    """Embedding 段连通性自检：SSRF 校验后实调 OpenAI 兼容 /embeddings。

    与 ``probe_endpoint``（Chat 段）结构一致，返回 {ok, status_code, latency_ms, detail}。
    未配置 embed_model 的租户不调用本函数——那种情况由调用方判为「复用 Chat 段」。
    """
    try:
        assert_safe_base_url(base_url)
    except SSRFRejected as e:
        return {"ok": False, "status_code": None, "latency_ms": 0, "detail": f"SSRF 拦截：{e}"}

    transport = _transport or _httpx_embed_transport
    started = time.perf_counter()
    try:
        resp = await transport(base_url, api_key, model, timeout)
        latency = int((time.perf_counter() - started) * 1000)
        if hasattr(resp, "status_code"):
            sc = resp.status_code
            detail = f"HTTP {sc}"
            if sc == 401:
                detail = "密钥无效或缺失 (401)"
            elif sc == 403:
                detail = "无权限访问 (403)，检查密钥或账号额度"
            elif sc == 404:
                detail = "端点路径不存在 (404)，请检查 base_url 是否含 /v1，或该端点不支持 /embeddings"
            elif sc == 400:
                detail = "请求被拒 (400)，检查 embed 模型名是否被该端点支持"
            elif sc == 429:
                detail = "被限流 (429)：地址与密钥有效，仅速率/配额受限，稍后重试或提配额"
            return {"ok": sc < 400, "status_code": sc, "latency_ms": latency, "detail": detail}
        return resp
    except Exception as e:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": latency,
            "detail": f"{type(e).__name__}: {str(e)[:200]}",
        }


async def _httpx_embed_transport(base_url: str, api_key: str, model: str, timeout: float):
    import httpx

    url = base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "input": "ping"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, headers=headers, json=payload)


async def _httpx_transport(base_url: str, api_key: str, model: str, timeout: float):
    import httpx

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, headers=headers, json=payload)


async def invalidate_agent_cache(tenant_id: str) -> None:
    """配置变更后尽力清除 agent 侧 BYOM 解析缓存（``byom:cfg:{tenant_id}``）。

    复用部署环境共享的 Redis（web/agent 同实例）；未配置 ``REDIS_URL`` 时跳过，
    依赖 T-M2 的短 TTL 自愈。best-effort：失败不阻塞保存主流程。
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        await r.delete(f"byom:cfg:{tenant_id}")
        await r.aclose()
    except Exception as e:  # noqa: BLE001
        log.warning("byom agent cache invalidate failed: %s", e)
