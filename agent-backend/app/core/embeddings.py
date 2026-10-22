"""向量化：优先走与 LLM 同族的 OpenAI 兼容 /v1/embeddings；无密钥时降级为本地确定性向量。

真实模式依赖 openai 包（延迟导入）；dev 模式仅用标准库，可直接跑通。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

from app.core import config

_TOKEN = re.compile(r"\s+")


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


async def embed(texts: Sequence[str]) -> list[list[float]]:
    """批量向量化：真实模式调远程 embedding，否则本地 dev 向量。"""
    texts = list(texts)
    if not texts:
        return []
    if config.embed_ready():
        return await _remote_embed(texts)
    return [local_embed_one(t) for t in texts]


async def _remote_embed(texts: list[str]) -> list[list[float]]:
    from openai import AsyncOpenAI  # 延迟导入：dev 模式无需安装

    client = AsyncOpenAI(api_key=config.EMBED_API_KEY, base_url=config.EMBED_BASE_URL)
    resp = await client.embeddings.create(model=config.EMBED_MODEL, input=texts)
    items = sorted(resp.data, key=lambda d: d.index)
    return [list(map(float, d.embedding)) for d in items]


async def embed_one(text: str) -> list[float]:
    vecs = await embed([text])
    return vecs[0] if vecs else []
