"""W6 BYOM T-M2 单测：运行态解析 / 全局回落 / SSRF 闸门 / 缓存。

全部 hermetic：不连 Redis / PG / 真实 LLM。``resolve_llm_config`` 通过注入的
``loader`` / ``cache`` 完成全部逻辑，真实路径默认走 106 共享库与 Redis（由部署环境提供）。

密钥加解密复用 agent-backend/app/core/vendor_crypto.py（与 web-backend 字节兼容），
单测用 ``VENDOR_CREDENTIAL_KEY`` 固定 32 字节密钥造密文，验证「密文 → 明文」往返
与「坏密文 → 回落全局」。
"""
import asyncio
import json
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
    vlm_base_url=None,
    vlm_model=None,
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
        "vlm_base_url": vlm_base_url,
        "vlm_model": vlm_model,
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
    # VLM 段缺省：vlm_base_url 回落 chat base_url，vlm_model 空（未单独配）
    assert cfg.vlm_base_url.endswith("/compatible-mode/v1")
    assert cfg.vlm_model == ""
    assert tl.vlm_enabled(cfg) is False


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


# ---------------------------------------------------------------- VLM 段解析

def test_resolve_tenant_with_vlm():
    async def load(tid):
        return _tenant_row(
            "t9",
            vlm_base_url="https://vlm.example.com/v1",
            vlm_model="qwen-vl-max",
        )

    cfg = asyncio.run(resolve_llm_config("t9", loader=load, cache=tl._LocalCache()))
    assert cfg.vlm_model == "qwen-vl-max"
    assert cfg.vlm_base_url == "https://vlm.example.com/v1"
    assert cfg.vlm_base_url != cfg.chat_base_url  # 独立 VLM 端点
    assert tl.vlm_enabled(cfg) is True


def test_resolve_tenant_vlm_reuses_chat_base_when_blank():
    async def load(tid):
        return _tenant_row("t10", vlm_model="gpt-4o")  # 不填 vlm_base_url

    cfg = asyncio.run(resolve_llm_config("t10", loader=load, cache=tl._LocalCache()))
    assert cfg.vlm_model == "gpt-4o"
    assert cfg.vlm_base_url == cfg.chat_base_url  # 回落 chat base_url
    assert tl.vlm_enabled(cfg) is True


def test_resolve_tenant_vlm_falls_back_to_global(monkeypatch):
    # 租户配了 chat 但没配 vlm_model → 回落平台全局 VLM（保留接入 BYOM 前的拍照识题能力）
    monkeypatch.setattr(cfg_mod, "VLM_BASE_URL", "https://g-vlm.example/v1")
    monkeypatch.setattr(cfg_mod, "LLM_API_KEY", "sk-global")
    monkeypatch.setattr(cfg_mod, "VLM_MODEL", "global-vlm")

    async def load(tid):
        return _tenant_row("t11")  # 无 vlm_model

    cfg = asyncio.run(resolve_llm_config("t11", loader=load, cache=tl._LocalCache()))
    assert cfg.vlm_model == "global-vlm"
    assert cfg.vlm_base_url == "https://g-vlm.example/v1"
    assert tl.vlm_enabled(cfg) is True


def test_global_vlm_fields(monkeypatch):
    monkeypatch.setattr(cfg_mod, "VLM_BASE_URL", "https://g-vlm.example/v1")
    monkeypatch.setattr(cfg_mod, "LLM_API_KEY", "sk-global")
    monkeypatch.setattr(cfg_mod, "VLM_MODEL", "global-vlm")
    cfg = asyncio.run(resolve_llm_config(None, cache=tl._LocalCache()))
    assert cfg.vlm_model == "global-vlm"
    assert cfg.vlm_base_url == "https://g-vlm.example/v1"
    assert tl.vlm_enabled(cfg) is True


# ---------------------------------------------------------------- 缓存语义

class _SpyCache(tl._LocalCache):
    def __init__(self):
        super().__init__()
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        return await super().get(key)


def test_cache_hit_skips_loader():
    """缓存命中时配置字段不重复解析，但仍需查库解密密钥（方案 A：密钥不落盘）。"""
    calls = {"n": 0}

    async def load(tid):
        calls["n"] += 1
        return _tenant_row("t6")

    cache = _SpyCache()
    asyncio.run(resolve_llm_config("t6", loader=load, cache=cache))
    assert calls["n"] == 1
    # 第二次：配置字段命中缓存，但仍查库一次解密密钥
    asyncio.run(resolve_llm_config("t6", loader=load, cache=cache))
    assert calls["n"] == 2  # 方案 A：密钥不落盘，每次都要重新解密
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


# ---------------------------------------------------------------- SQLite 本地缓存后端

def test_sqlite_cache_roundtrip(tmp_path):
    async def go():
        c = tl._SqliteCache(str(tmp_path / "byom.db"), ttl=100)
        try:
            await c.set("k", {"a": 1, "b": "x"})
            assert await c.get("k") == {"a": 1, "b": "x"}
            await c.delete("k")
            assert await c.get("k") is None
        finally:
            await c.close()

    asyncio.run(go())


def test_sqlite_cache_expiry(tmp_path):
    async def go():
        c = tl._SqliteCache(str(tmp_path / "byom.db"), ttl=0)  # 立即过期
        try:
            await c.set("k", {"a": 1})
            assert await c.get("k") is None
        finally:
            await c.close()

    asyncio.run(go())


def test_sqlite_cache_persists_across_instances(tmp_path):
    """SQLite 落盘：不同实例（进程/容器重启后）读同一文件仍能命中——「本地缓存提速」的根基。"""
    db = str(tmp_path / "byom.db")

    async def go():
        c1 = tl._SqliteCache(db, ttl=100)
        c2 = tl._SqliteCache(db, ttl=100)
        try:
            await c1.set("cfg:t1", {"source": "tenant", "chat_model": "qwen-max"})
            val = await c2.get("cfg:t1")
            assert val == {"source": "tenant", "chat_model": "qwen-max"}
        finally:
            await c1.close()
            await c2.close()

    asyncio.run(go())


def test_resolve_uses_sqlite_cache(tmp_path):
    """解析结果写入 SQLite 缓存（不含密钥）；第二次解析配置字段命中本地文件，
    但仍查库一次解密密钥（方案 A：密钥不落盘）。"""
    calls = {"n": 0}

    async def load(tid):
        calls["n"] += 1
        return _tenant_row("t-sqlite")

    async def go():
        c = tl._SqliteCache(str(tmp_path / "byom.db"))
        try:
            cfg1 = await resolve_llm_config("t-sqlite", loader=load, cache=c)
            assert cfg1.source == "tenant"
            cfg2 = await resolve_llm_config("t-sqlite", loader=load, cache=c)
            assert cfg2.chat_model == cfg1.chat_model
        finally:
            await c.close()

    asyncio.run(go())
    assert calls["n"] == 2  # 方案 A：首次查库+解密，第二次仅查库解密（配置字段命中缓存）


def test_cache_never_persists_api_key(tmp_path):
    """方案 A 的安全红线：写进 SQLite 的缓存值**不得**含任何 api_key 字段。

    这是「密钥不落盘」的硬保证——SQLite 文件落在容器磁盘上，一旦被拖走
    或挂载出来，绝不能泄露学校的大模型密钥。
    """
    db = str(tmp_path / "byom.db")

    async def load(tid):
        return _tenant_row("t-secret")

    async def go():
        c = tl._SqliteCache(db)
        try:
            cfg = await resolve_llm_config("t-secret", loader=load, cache=c)
            # 内存里拿到的是解密后的真密钥（业务需要）
            assert cfg.chat_api_key == "sk-tenant-secret"
            raw = await c.get(tl.CACHE_PREFIX + "t-secret")
            assert raw is not None
        finally:
            await c.close()
        return raw

    raw = asyncio.run(go())
    assert "chat_api_key" not in raw and "embed_api_key" not in raw
    assert not any("api_key" in k for k in raw)
    # 明文密钥不得出现在序列化后的任何一处
    assert "sk-tenant-secret" not in json.dumps(raw, ensure_ascii=False)


def test_cache_hit_refills_key_from_db():
    """缓存命中时密钥从 DB 回填；若租户已被禁用/删除，缓存失效并回落全局。"""

    async def load_enabled(tid):
        return _tenant_row("t9")

    async def load_disabled(tid):
        return _tenant_row("t9", enabled=False)

    async def go():
        cache = tl._LocalCache()
        cfg1 = await resolve_llm_config("t9", loader=load_enabled, cache=cache)
        assert cfg1.chat_api_key == "sk-tenant-secret"

        # 命中缓存 + 回填密钥
        cfg2 = await resolve_llm_config("t9", loader=load_enabled, cache=cache)
        assert cfg2.source == "tenant"
        assert cfg2.chat_api_key == "sk-tenant-secret"
        assert cfg2.chat_model == "qwen-max"

        # 租户被禁用：缓存应被清掉，回落全局，且不抛 TypeError
        cfg3 = await resolve_llm_config("t9", loader=load_disabled, cache=cache)
        assert cfg3.source == "global"
        assert tl.CACHE_PREFIX + "t9" not in cache._d

    asyncio.run(go())

