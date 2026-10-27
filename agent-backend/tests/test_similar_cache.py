"""T-D1 补位：变式题（similar）必须接请求级缓存。

背景（2026-10-27 线上实测）：``llm.suggest_similar_detailed`` 此前**裸调 LLM 未接
cached_solve**，同题每次都重烧 7~17s，是解题链路最大的一块无谓开销（主解 solve
第二次已能 0.0s 命中）。本文件锁定"同题第二次命中缓存、不再调 LLM"，
以及"生成失败不进缓存"两条口径。
"""

import asyncio

import app.core.llm as llm
import app.core.llm_cache as llm_cache
from app.core.tenant_llm import LLMConfig

_SIM_JSON = '{"similar": ["变式一：求 g(x)=2x^2 在 x=1 处的导数", "变式二"]}'


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})]
        self.usage = None


class _FakeClient:
    """记录调用次数的假 OpenAI 客户端（只实现 chat.completions.create）。"""

    def __init__(self, calls):
        self._calls = calls

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kw):
        self._calls.append(kw)
        return _Resp(_SIM_JSON)


def _cfg() -> LLMConfig:
    return LLMConfig(
        chat_base_url="https://example.invalid/v1",
        chat_api_key="k",
        chat_model="m",
        embed_base_url="",
        embed_api_key="",
        embed_model="",
        embed_dim=0,
        model_simple="",
        model_hard="",
        rag_top_k=4,
        source="tenant",
        tenant_id="school_pilot_01",
    )


def test_similar_hits_cache_on_second_call(monkeypatch):
    """同题第二次应命中缓存，LLM 只被调一次。"""
    calls: list = []
    cfg = _cfg()
    store = llm_cache.MemoryStore()
    monkeypatch.setattr(llm.tenant_llm, "make_client", lambda _cfg: _FakeClient(calls))
    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    monkeypatch.setattr(llm.config, "embed_ready", lambda: False)  # 不走真实 embedding
    monkeypatch.setattr(llm_cache, "get_default_store", lambda: store)
    # 单测默认 CACHE_ENABLED=false（hermetic），这里显式开缓存 + 用内存 store 兜住
    monkeypatch.setattr(llm_cache.config, "cache_enabled", lambda: True)

    async def run():
        r1 = await llm.suggest_similar_detailed("求 f(x)=x^2 在 x=3 处的导数", None, llm_cfg=cfg)
        r2 = await llm.suggest_similar_detailed("求 f(x)=x^2 在 x=3 处的导数", None, llm_cfg=cfg)
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1["degraded"] is False
    assert r1["similar"][0].startswith("变式一")
    assert r2 == r1
    assert len(calls) == 1, "同题第二次必须命中缓存，不再烧 LLM"


def test_similar_failure_is_not_cached(monkeypatch):
    """生成失败（异常）不进缓存：下一次必须重新尝试，不能把降级固化。"""
    calls: list = []

    class _Boom(_FakeClient):
        async def create(self, **kw):
            calls.append(kw)
            raise RuntimeError("403 quota")

    cfg = _cfg()
    store = llm_cache.MemoryStore()
    monkeypatch.setattr(llm.tenant_llm, "make_client", lambda _cfg: _Boom(calls))
    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    monkeypatch.setattr(llm.config, "embed_ready", lambda: False)
    monkeypatch.setattr(llm_cache, "get_default_store", lambda: store)
    monkeypatch.setattr(llm_cache.config, "cache_enabled", lambda: True)

    async def run():
        return await llm.suggest_similar_detailed("配额耗尽的题", None, llm_cfg=cfg)

    out = asyncio.run(run())
    # 失败 → 回落检索片段，且带 degraded 标记（旁路能力，不污染主解 degraded）
    assert out["degraded"] is True
    assert len(calls) == 1
