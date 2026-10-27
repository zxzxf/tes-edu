"""向量化：优先走与 LLM 同族的 OpenAI 兼容 /v1/embeddings；无密钥时降级为本地确定性向量。

真实模式依赖 openai 包（延迟导入）；dev 模式仅用标准库，可直接跑通。

W6 BYOM：``embed`` / ``embed_one`` 支持传入 ``embed_cfg``（租户级 embedding 配置）。
传入且 ``embed_model`` 非空时使用其 base_url / api_key / model / dim，否则回落全局
``config.EMBED_*``。检索（RAG）与学生端缓存近似命中因此都能用学校配置的 embedding 模型，
而非写死平台全局——与「所有面向学生端的模型变量都从学校 BYOM 配置取」一致。
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from app.core import config

_TOKEN = re.compile(r"\s+")


@dataclass
class EmbedConfig:
    """租户级 embedding 配置（由 LLMConfig.embed_* 构造，或单独传入）。"""

    base_url: str
    api_key: str
    model: str
    dim: int


def _resolve_embed_cfg(embed_cfg: Optional[EmbedConfig]) -> Optional[dict]:
    """把传入的 embed_cfg 归一为 dict；模型名为空视为「未配置」回落全局。

    兼容两类入参：
    - :class:`EmbedConfig`（字段 ``model`` / ``base_url`` / ``api_key`` / ``dim``）；
    - 任意带 ``embed_*`` 属性的对象（如 ``LLMConfig``：``embed_model`` 等）。
    优先读 ``embed_*`` 命名，缺失再读无前缀命名，便于调用方直接透传。
    """
    if embed_cfg is None:
        return None
    model = getattr(embed_cfg, "embed_model", None) or getattr(embed_cfg, "model", "") or ""
    if not model:
        return None
    return {
        "base_url": getattr(embed_cfg, "embed_base_url", None)
        or getattr(embed_cfg, "base_url", "")
        or config.EMBED_BASE_URL,
        "api_key": getattr(embed_cfg, "embed_api_key", None)
        or getattr(embed_cfg, "api_key", "")
        or config.EMBED_API_KEY,
        "model": model,
        "dim": int(getattr(embed_cfg, "embed_dim", 0) or getattr(embed_cfg, "dim", 0) or config.EMBED_DIM),
    }


def local_embed_one(text: str, dim: int | None = None) -> list[float]:
    """本地确定性向量（dev 回退）：字符 bigram 哈希投影 + L2 归一化。

    仅用于无 embedding 服务时把链路跑通；语义质量有限，生产请配置真实 embedding。
    """
    dim = dim or config.EMBED_DIM
    vec = [0.0] * dim
    s = _TOKEN.sub("", text or "")
    if not s:
        return vec
    grams = [s[i:i + 2] for i in range(len(s) - 1)] or [s]
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0 if (h // dim) % 2 == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def embed(
    texts: Sequence[str], *, embed_cfg: Optional[EmbedConfig] = None
) -> list[list[float]]:
    """批量向量化：真实模式调远程 embedding，否则本地 dev 向量。

    ``embed_cfg`` 非空且配置了模型 → 走租户级 embedding；否则回落全局 ``config.EMBED_*``；
    二者皆无 → 本地确定性向量。
    """
    texts = list(texts)
    if not texts:
        return []
    cfg = _resolve_embed_cfg(embed_cfg)
    # 未传租户配置时，仍优先用全局 embedding（保持接入 BYOM 前行为）
    if cfg is None and config.embed_ready():
        cfg = {
            "base_url": config.EMBED_BASE_URL,
            "api_key": config.EMBED_API_KEY,
            "model": config.EMBED_MODEL,
            "dim": config.EMBED_DIM,
        }
    if cfg is not None:
        return await _remote_embed(texts, cfg)
    dim = cfg["dim"] if cfg else config.EMBED_DIM
    return [local_embed_one(t, dim) for t in texts]


async def _remote_embed(texts: list[str], cfg: dict) -> list[list[float]]:
    from openai import AsyncOpenAI  # 延迟导入：dev 模式无需安装

    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    out: list[list[float]] = []
    size = max(1, config.EMBED_BATCH)
    # embedding 接口有单次文本数上限，超出需分批（灌库时文本量远大于上限）
    for i in range(0, len(texts), size):
        chunk = texts[i:i + size]
        resp = await client.embeddings.create(model=cfg["model"], input=chunk)
        items = sorted(resp.data, key=lambda d: d.index)
        out.extend([list(map(float, d.embedding)) for d in items])
    return out


async def embed_one(text: str, *, embed_cfg: Optional[EmbedConfig] = None) -> list[float]:
    vecs = await embed([text], embed_cfg=embed_cfg)
    return vecs[0] if vecs else []
