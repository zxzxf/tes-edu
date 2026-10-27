"""W6 BYOM T-M2 单测：运行态解析 / 全局回落 / SSRF 闸门 / 缓存。

全部 hermetic：不连 Redis / PG / 真实 LLM。``resolve_llm_config`` 通过注入的
``loader`` / ``cache`` 完成全部逻辑，真实路径默认走 106 共享库与 Redis（由部署环境提供）。

密钥加解密复用 agent-backend/app/core/vendor_crypto.py（与 web-backend 字节兼容），
单测用 ``VENDOR_CREDENTIAL_KEY`` 固定 32 字节密钥造密文，验证「密文 → 明文」往返
与「坏密文 → 回落全局」。
"""
import asyncio
import os

import pytest

from app.core import config as cfg_mod
from app.core import tenant_llm as tl
from app.core.tenant_llm import (
    LLMConfig,
    SSRFRejected,
    assert_safe_base_url,
    invalidate_tenant,
    resolve_llm_config,
)
from app.core.vendor_crypto import encrypt


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    # 固定 32 字节（hex）主密钥，保证加解密可在单测内往返
    monkeypatch.setenv("VENDOR_CREDENTIAL_KEY", "a" * 64)
    monkeypatch.delenv("BYOM_ALLOWED_DOMAINS", raising=False)
    yield


# ---------------------------------------------------------------- SSRF 闸门

def test_ssrf_rejects_http():
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("http://api.openai.com/v1")


def test_ssrf_rejects_localhost():
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("https://localhost/v1")


def test_ssrf_rejects_127():
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("https://127.0.0.1/v1")


def test_ssrf_rejects_rfc1918():
    for h in ["10.0.0.5", "172.16.0.1", "192.168.1.1"]:
        with pytest.raises(SSRFRejected):
            assert_safe_base_url(f"https://{h}/v1")


def test_ssrf_rejects_metadata():
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("https://169.254.169.254/latest/meta-data")


def test_ssrf_rejects_ipv6_local():
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("https://[::1]/v1")


def test_ssrf_allows_public_https():
    assert (
        assert_safe_base_url("https://api.openai.com/v1")
        == "https://api.openai.com/v1"
    )


def test_ssrf_allowlist(monkeypatch):
    monkeypatch.setenv("BYOM_ALLOWED_DOMAINS", "*.aliyuncs.com, .tencent.com")
    # 命中白名单
    assert_safe_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert_safe_base_url("https://api.hunyuan.cloud.tencent.com/v1")
    # 不在白名单 → 拒绝
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("https://api.openai.com/v1")


# ---------------------------------------------------------------- 行构造器

def _tenant_row(
    tenant_id,
    *,
    enabled=True,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    model="qwen-max",
    embed_model="text-embedding-v3",
    embed_dim=1024,
    model_simple=None,
    model_hard=None,
    rag_top_k=4,
    embed_base_url=None,
    embed_api_key=None,
):
    return {
        "tenant_id": tenant_id,
        "provider": "aliyun",
        "base_url": base_url,
        "api_key_enc": encrypt("sk-tenant-secret"),
        "model": model,
        "embed_base_url": embed_base_url,
        "embed_api_key_enc": encrypt(embed_api_key) if embed_api_key else None,
        "embed_model": embed_model,
        "embed_dim": embed_dim,
        "model_simple": model_simple,
        "model_hard": model_hard,
        "enabled": enabled,
        "rag_top_k": rag_top_k,
    }


# ---------------------------------------------------------------- 解析主路径

def test_resolve_tenant_full():
    async def load(tid):
        return _tenant_row("t1")

    cfg = asyncio.run(resolve_llm_config("t1", loader=load, cache=tl._LocalCache()))
    assert isinstance(cfg, LLMConfig)
    assert cfg.source == "tenant"
    assert cfg.chat_api_key == "sk-tenant-secret"  # 密文已正确解密
    assert cfg.chat_model == "qwen-max"
    assert cfg.chat_base_url.endswith("/compatible-mode/v1")
    assert cfg.embed_model == "text-embedding-v3"
    assert cfg.embed_dim == 1024
    assert cfg.provider == "aliyun"
    assert cfg.rag_top_k == 4
    assert cfg.tenant_id == "t1"


def test_resolve_no_tenant_falls_back():
    cfg = asyncio.run(resolve_llm_config(None, cache=tl._LocalCache()))
    assert cfg.source == "global"


def test_resolve_disabled_falls_back():
    async def load(tid):
        return _tenant_row("t2", enabled=False)

    cfg = asyncio.run(resolve_llm_config("t2", loader=load, cache=tl._LocalCache()))
    assert cfg.source == "global"


def test_resolve_ssrf_blocked_falls_back():
    async def load(tid):
        return _tenant_row("t3", base_url="https://10.0.0.9/v1")

    cfg = asyncio.run(resolve_llm_config("t3", loader=load, cache=tl._LocalCache()))
    assert cfg.source == "global"


def test_resolve_bad_cipher_falls_back():
    async def load(tid):
        row = _tenant_row("t4")
        row["api_key_enc"] = b"not-a-real-ciphertext-blob"
        return row

    cfg = asyncio.run(resolve_llm_config("t4", loader=load, cache=tl._LocalCache()))
    assert cfg.source == "global"


def test_resolve_embed_segment_override():
    async def load(tid):
        return _tenant_row(
            "t5",
            embed_base_url="https://embedding.example.com/v1",
            embed_api_key="sk-embed-only",
            embed_model="my-embed",
        )

    cfg = asyncio.run(resolve_llm_config("t5", loader=load, cache=tl._LocalCache()))
    assert cfg.embed_base_url == "https://embedding.example.com/v1"
    assert cfg.embed_api_key == "sk-embed-only"
    assert cfg.embed_model == "my-embed"
    # chat 段不受影响
    assert cfg.chat_api_key == "sk-tenant-secret"


def test_global_config_fields(monkeypatch):
    monkeypatch.setattr(cfg_mod, "LLM_BASE_URL", "https://g.example/v1")
    monkeypatch.setattr(cfg_mod, "LLM_API_KEY", "sk-global")
    monkeypatch.setattr(cfg_mod, "LLM_MODEL", "g-model")
    cfg = asyncio.run(resolve_llm_config(None, cache=tl._LocalCache()))
    assert cfg.chat_base_url == "https://g.example/v1"
    assert cfg.chat_api_key == "sk-global"
    assert cfg.chat_model == "g-model"
    assert cfg.source == "global"


# ---------------------------------------------------------------- 缓存语义

class _SpyCache(tl._LocalCache):
    def __init__(self):
        super().__init__()
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        return await super().get(key)


def test_cache_hit_skips_loader():
    calls = {"n": 0}

    async def load(tid):
        calls["n"] += 1
        return _tenant_row("t6")

    cache = _SpyCache()
    asyncio.run(resolve_llm_config("t6", loader=load, cache=cache))
    assert calls["n"] == 1
    # 第二次：命中缓存，不再查库
    asyncio.run(resolve_llm_config("t6", loader=load, cache=cache))
    assert calls["n"] == 1
    assert cache._d[tl.CACHE_PREFIX + "t6"][1]["source"] == "tenant"


def test_global_not_cached():
    calls = {"n": 0}

    async def load(tid):
        calls["n"] += 1
        return None

    cache = tl._LocalCache()
    asyncio.run(resolve_llm_config("t7", loader=load, cache=cache))
    assert calls["n"] == 1
    # 全局回落不缓存 → 再次查库
    asyncio.run(resolve_llm_config("t7", loader=load, cache=cache))
    assert calls["n"] == 2


def test_invalidate_clears_cache():
    async def load(tid):
        return _tenant_row("t8")

    cache = tl._LocalCache()
    asyncio.run(resolve_llm_config("t8", loader=load, cache=cache))
    assert tl.CACHE_PREFIX + "t8" in cache._d
    asyncio.run(invalidate_tenant("t8", cache=cache))
    assert tl.CACHE_PREFIX + "t8" not in cache._d
