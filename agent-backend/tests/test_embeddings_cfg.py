"""embeddings 按 embed_cfg 路由：租户配置优先，否则全局，再否则本地确定性向量。

W6 BYOM：RAG 检索与学生端缓存近似命中都该用学校配置的 embedding 模型，而非平台全局。
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core import embeddings
from app.core.embeddings import EmbedConfig


def test_embed_uses_tenant_cfg(monkeypatch):
    captured = {}

    async def fake_remote(texts, cfg):
        captured["cfg"] = cfg
        return [[0.1] * (cfg["dim"] or 8) for _ in texts]

    monkeypatch.setattr(embeddings, "_remote_embed", fake_remote)
    ec = EmbedConfig(
        base_url="https://e.example/v1", api_key="sk-e", model="tenant-embed", dim=8
    )
    out = asyncio.run(embeddings.embed(["hello"], embed_cfg=ec))
    assert captured["cfg"]["model"] == "tenant-embed"
    assert captured["cfg"]["base_url"] == "https://e.example/v1"
    assert len(out) == 1 and len(out[0]) == 8


def test_embed_falls_back_to_global_when_no_cfg(monkeypatch):
    captured = {}

    async def fake_remote(texts, cfg):
        captured["cfg"] = cfg
        return [[0.1] * 4 for _ in texts]

    monkeypatch.setattr(embeddings, "_remote_embed", fake_remote)
    monkeypatch.setattr(embeddings.config, "embed_ready", lambda: True)
    monkeypatch.setattr(embeddings.config, "EMBED_BASE_URL", "https://g/v1")
    monkeypatch.setattr(embeddings.config, "EMBED_API_KEY", "sk-g")
    monkeypatch.setattr(embeddings.config, "EMBED_MODEL", "global-embed")
    monkeypatch.setattr(embeddings.config, "EMBED_DIM", 4)
    out = asyncio.run(embeddings.embed(["hi"]))  # 无 embed_cfg
    assert captured["cfg"]["model"] == "global-embed"
    assert len(out) == 1


def test_embed_local_when_nothing_ready(monkeypatch):
    monkeypatch.setattr(embeddings.config, "embed_ready", lambda: False)
    out = asyncio.run(embeddings.embed(["x"]))
    assert len(out) == 1
    v = out[0]
    assert len(v) == embeddings.config.EMBED_DIM
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_embed_empty_returns_empty():
    assert asyncio.run(embeddings.embed([])) == []


def test_embed_one_routes_cfg(monkeypatch):
    captured = {}

    async def fake_remote(texts, cfg):
        captured["cfg"] = cfg
        return [[0.2] * 4]

    monkeypatch.setattr(embeddings, "_remote_embed", fake_remote)
    ec = EmbedConfig(base_url="b", api_key="k", model="m", dim=4)
    v = asyncio.run(embeddings.embed_one("q", embed_cfg=ec))
    assert captured["cfg"]["model"] == "m"
    assert v == [0.2] * 4
