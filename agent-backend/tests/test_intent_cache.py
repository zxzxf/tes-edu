"""T-C5 意图分类缓存：同题不再重烧 LLM。"""
import asyncio
from app.core import intent, llm_cache


def test_intent_classification_is_cached():
    """规则未命中且问句够长时，第一次烧 LLM，第二次命中缓存。"""
    store = llm_cache.MemoryStore()
    original_get = llm_cache.get_default_store
    llm_cache.get_default_store = lambda: store
    original_enabled = llm_cache.config.cache_enabled
    llm_cache.config.cache_enabled = lambda: True

    calls = []

    async def fake_classify_llm(question, context=None, *, llm_cfg=None):
        calls.append(question)
        return "explain"

    original_fn = intent.classify_intent_llm
    intent.classify_intent_llm = fake_classify_llm

    try:
        q = "求 f(x)=e^x+x² 在 x=2 处的导数并给出详细过程"  # 不含显式意图关键词

        async def run():
            # 第一次：回源烧 LLM
            r1 = await intent.resolve_response_mode(q)
            assert r1 == ("explain", "explain", "llm")
            assert len(calls) == 1

            # 第二次：命中缓存，不再烧 LLM
            r2 = await intent.resolve_response_mode(q)
            assert r2[0] == "explain"
            assert r2[2] == "cache"
            assert len(calls) == 1, "同题第二次应命中缓存，不再调 LLM"

        asyncio.run(run())
    finally:
        llm_cache.get_default_store = original_get
        llm_cache.config.cache_enabled = original_enabled
        intent.classify_intent_llm = original_fn


def test_short_question_skips_llm_and_cache():
    """短问句（<30 字）直接默认引导档，不烧 LLM 也不落缓存。"""
    store = llm_cache.MemoryStore()
    original_get = llm_cache.get_default_store
    llm_cache.get_default_store = lambda: store

    calls = []

    async def fake_classify_llm(question, context=None, *, llm_cfg=None):
        calls.append(question)
        return "direct"

    original_fn = intent.classify_intent_llm
    intent.classify_intent_llm = fake_classify_llm

    try:
        q = "求导数"  # 3 字，不含任何意图关键词

        async def run():
            r = await intent.resolve_response_mode(q)
            assert r == ("guide", "guide", "default")
            assert len(calls) == 0, "短问句不应触发 LLM 分类"

        asyncio.run(run())
    finally:
        llm_cache.get_default_store = original_get
        intent.classify_intent_llm = original_fn


def test_rule_hit_skips_llm_and_cache():
    """规则命中的问句直接返回，不烧 LLM 也不落缓存。"""
    store = llm_cache.MemoryStore()
    original_get = llm_cache.get_default_store
    llm_cache.get_default_store = lambda: store

    calls = []

    async def fake_classify_llm(question, context=None, *, llm_cfg=None):
        calls.append(question)
        return "explain"

    original_fn = intent.classify_intent_llm
    intent.classify_intent_llm = fake_classify_llm

    try:
        q = "直接给答案"  # 含直给关键词

        async def run():
            r = await intent.resolve_response_mode(q)
            assert r == ("direct", "quick", "rules")
            assert len(calls) == 0, "规则命中不应触发 LLM 分类"

        asyncio.run(run())
    finally:
        llm_cache.get_default_store = original_get
        intent.classify_intent_llm = original_fn
