"""Embedding 接入层（M0-1 启用）。

录题/导入时把题目文本向量化，双写 ``course_kb_embeddings``，
供 agent-backend 的 RAG 检索（``app/core/rag.py``）消费。

解析顺序（与 agent-backend ``tenant_llm.resolve_for`` 同口径）：

1. 租户 BYOM ``tenant_model_config`` 的 embedding 段——**``embed_model`` 非空才算
   配过 embed 段**；endpoint 缺省复用 chat 段 ``base_url``，密钥缺省复用 chat 段
   （``api_key_enc``）。这与 VLM 段「密钥复用 chat、空 model 视为没配」一致。
2. 全局 env ``EMBED_MODEL / EMBED_API_BASE / EMBED_API_KEY``。

**红线（M0-1 双写约束）**：任何失败都不抛穿调用方——未配置、401、403、429、
超时、返回维度不符，一律返回 ``ok=False`` 并带上原因。调用方据此把题目留在
``status=draft``，由 M0-4 回填脚本补向量；绝不让「向量没生成」阻塞题目入库，
也绝不把错误维度的向量写进 ``VECTOR(1024)``（那会污染检索，且 pgvector 直接报错）。
"""
from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from app.core.config import settings
from app.core.ssrf import SSRFRejected, assert_safe_base_url

log = logging.getLogger(__name__)

#: 与 ``course_kb_embeddings.embedding`` 列宽一致；换模型必须同步改列与 hnsW 索引
EMBED_DIM = 1024
#: 单次请求批量上限：多数兼容端点对 input 数组长度有限制，且失败时整批作废，
#: 批小一点让「个别题失败」不至于拖垮整批
EMBED_BATCH = 10
EMBED_TIMEOUT = 20.0


async def resolve_config_async(db, tenant_id: Optional[str]) -> dict:
    """异步版：租户 BYOM 优先，未配则回落全局 env。"""
    empty = {"source": "none", "base_url": "", "api_key": "", "model": ""}

    if db is not None and tenant_id:
        try:
            from sqlalchemy import select

            from app.models.byom import TenantModelConfig
            from app.services import byom as byom_svc

            obj = (
                await db.execute(
                    select(TenantModelConfig).where(
                        TenantModelConfig.tenant_id == tenant_id
                    )
                )
            ).scalars().first()
            if obj is not None and getattr(obj, "embed_model", None):
                api_key = byom_svc.decrypt_embed_api_key(obj.embed_api_key_enc)
                if not api_key:
                    # 未单独配 embed 密钥 → 复用 chat 段（与 VLM 段同一约定）
                    api_key = byom_svc.decrypt_api_key(obj.api_key_enc)
                return {
                    "source": "tenant",
                    "base_url": obj.embed_base_url or obj.base_url,
                    "api_key": api_key or "",
                    "model": obj.embed_model,
                }
        except Exception:  # noqa: BLE001 配置读不出来只降级，绝不影响入库
            log.warning("租户 embedding 配置解析失败，回落全局", exc_info=True)

    if settings.embed_model and settings.embed_api_base:
        return {
            "source": "global",
            "base_url": settings.embed_api_base,
            "api_key": settings.embed_api_key,
            "model": settings.embed_model,
        }
    return empty


async def _http_transport(base_url: str, api_key: str, model: str, texts: List[str], timeout: float):
    """OpenAI 兼容 ``/embeddings`` 调用。"""
    url = base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "input": texts}
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, headers=headers, json=payload)


def _parse_vectors(payload: dict, expected: int) -> List[List[float]]:
    """按 ``index`` 排序取出向量，保证与输入顺序一一对应。"""
    data = payload.get("data") or []
    if len(data) != expected:
        raise ValueError(f"向量条数不符：期望 {expected}，返回 {len(data)}")
    ordered = sorted(data, key=lambda d: d.get("index", 0))
    vectors: List[List[float]] = []
    for item in ordered:
        vec = item.get("embedding")
        if not isinstance(vec, list) or not vec:
            raise ValueError("返回体缺少 embedding 数组")
        if len(vec) != EMBED_DIM:
            raise ValueError(f"向量维度不符：期望 {EMBED_DIM}，返回 {len(vec)}")
        vectors.append([float(x) for x in vec])
    return vectors


async def embed_texts(
    texts: List[str],
    *,
    db=None,
    tenant_id: Optional[str] = None,
    transport=None,
    config: Optional[dict] = None,
) -> dict:
    """把一批文本向量化。

    :returns: ``{"ok", "vectors", "model", "source", "reason"}``。
        ``ok=False`` 时 ``vectors`` 为 ``None``，``reason`` 给出可读原因
        （写进录题响应，让教师知道「为什么这题还没进检索」）。
    """
    if not texts:
        return {"ok": True, "vectors": [], "model": "", "source": "none", "reason": ""}

    cfg = config or await resolve_config_async(db, tenant_id)
    if cfg["source"] == "none" or not cfg.get("model"):
        return {
            "ok": False,
            "vectors": None,
            "model": "",
            "source": "none",
            "reason": "未配置 embedding 端点（租户 BYOM 与全局 EMBED_* 均为空）",
        }

    try:
        assert_safe_base_url(cfg["base_url"])
    except SSRFRejected as e:
        return {
            "ok": False,
            "vectors": None,
            "model": cfg["model"],
            "source": cfg["source"],
            "reason": f"embedding 端点未通过 SSRF 校验：{e}",
        }

    transport = transport or _http_transport
    vectors: List[List[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        chunk = texts[start : start + EMBED_BATCH]
        try:
            resp = await transport(
                cfg["base_url"], cfg["api_key"], cfg["model"], chunk, EMBED_TIMEOUT
            )
            status = getattr(resp, "status_code", None)
            if status is not None and status >= 400:
                hint = {
                    401: "密钥无效或缺失 (401)",
                    403: "无权限/额度耗尽 (403)",
                    404: "端点无 /embeddings (404)，检查 base_url 是否含 /v1",
                    429: "被限流 (429)，配额不足",
                }.get(status, f"HTTP {status}")
                return {
                    "ok": False,
                    "vectors": None,
                    "model": cfg["model"],
                    "source": cfg["source"],
                    "reason": f"embedding 调用失败：{hint}",
                }
            payload = resp.json() if hasattr(resp, "json") else resp
            vectors.extend(_parse_vectors(payload, len(chunk)))
        except Exception as e:  # noqa: BLE001 红线：绝不抛穿，题目照常入库
            return {
                "ok": False,
                "vectors": None,
                "model": cfg["model"],
                "source": cfg["source"],
                "reason": f"embedding 调用异常：{type(e).__name__}: {str(e)[:160]}",
            }

    return {
        "ok": True,
        "vectors": vectors,
        "model": cfg["model"],
        "source": cfg["source"],
        "reason": "",
    }


def build_content(stem: str, analysis: Optional[str]) -> str:
    """送检索的文本 = 题干 + 解析（与 import_bank 同口径）。"""
    return "\n".join(x for x in [stem or "", analysis or ""] if x.strip())
