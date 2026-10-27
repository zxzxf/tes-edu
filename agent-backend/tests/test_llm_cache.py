"""T-D1 请求级缓存与去重：缓存键 / 命中 / 近似命中 / TTL / 键隔离。

验收对应（计划书）：
- 命中/未命中/近似命中三态 + TTL 过期；
- 重复请求二次不调 LLM（mock 计数断言）；
- 缓存键含 hint_level（同题不同 Hint 级不得混）。

隔离约定（本文件所有用例均 hermetic）：
- 不连 Redis / PG / 真实 embedding 接口，一律用 ``MemoryStore`` + 固定向量；
- ``config.embed_ready()`` 用 ``_approx(on)`` 显式开关，避免隐式打网络。
"""
import asyncio
import time

import app.core.embeddings as emb_mod
import app.core.llm_cache as lc
from app.core.llm_cache import MemoryStore, cache_key, cache_namespace, cached_solve

_ORIG_EMBED_READY = lc.config.embed_ready
_ORIG_EMBED = emb_mod.embed


class _approx:
    """上下文管理器：显式开关'近似命中'分支，并注入固定 embedding。

    - ``on=False``：embed_ready() -> False，完全不碰 embedding（精确命中路径）；
    - ``on=True``：embed_ready() -> True，embed 走注入的假实现（不联网）。
    """

    def __init__(self, on: bool, vectors=None):
        self.on = on
        self.vectors = vectors

    def __enter__(self):
        lc.config.embed_ready = (lambda: True) if self.on else (lambda: False)
        if self.on:
            if self.vectors is None:
                emb_mod.embed = lambda texts, **kw: asyncio.sleep(0, result=[[0.6] * 64 for _ in texts])
            else:
                emb_mod.embed = lambda texts, **kw: asyncio.sleep(0, result=[list(v) for v in self.vectors])
        return self

    def __exit__(self, *exc):
        lc.config.embed_ready = _ORIG_EMBED_READY
        emb_mod.embed = _ORIG_EMBED
        return False


# ----------------------------------------------------------------- 缓存键

def test_cache_key_includes_hint_level():
    k0 = cache_key(subject="数学", mode="guide", hint_level=0, question="求导")
    k1 = cache_key(subject="数学", mode="guide", hint_level=1, question="求导")
    assert k0 != k1  # 同题不同 Hint 级不得混
    # 同题同档确定性
    assert k0 == cache_key(subject="数学", mode="guide", hint_level=0, question="求导")
    # content_hash 优先于问题文本；键中不含明文问题
    kc = cache_key(subject="数学", mode="guide", hint_level=0, question="求导", content_hash="h123")
    assert "ch:h123" in kc
    assert "求导" not in kc
    # 主解（hint_level=None）与 Hint L0 不得共键
    assert k0 != cache_key(subject="数学", mode="guide", hint_level=None, question="求导")


def test_cache_key_variant_separates_contexts():
    """同题同级但上下文不同（如 Hint 的对话历史/步骤）必须分键。"""
    base = dict(subject="数学", mode="guide", hint_level=1, question="Q")
    a = cache_key(**base, variant=None)
    b = cache_key(**base, variant='{"history": ["我先试试"]}')
    c = cache_key(**base, variant='{"history": ["换个思路"]}')
    assert a != b != c


def test_cache_namespace_isolates_tiers():
    assert cache_namespace(subject="数学", mode="guide", hint_level=0) != cache_namespace(
        subject="数学", mode="guide", hint_level=1
    )
    assert cache_namespace(subject="数学", mode="guide", hint_level=0) != cache_namespace(
        subject="数学", mode="explain", hint_level=0
    )
    assert cache_namespace(subject="数学", mode="guide", hint_level=0) != cache_namespace(
        subject="物理", mode="guide", hint_level=0
    )


# ------------------------------------------------------- 命中 / 未命中 / TTL

def test_miss_then_hit_calls_llm_once():
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append((q, m))
        return {"answer": "A", "steps": ["s1"]}

    async def run():
        with _approx(False):
            await cached_solve(fake_remote, "Q1", None, "explain", store=store)
            await cached_solve(fake_remote, "Q1", None, "explain", store=store)

    asyncio.run(run())
    assert len(calls) == 1, "二次相同请求应命中缓存，不再调 LLM"


def test_ttl_expiry_recaches():
    store = MemoryStore(ttl=1)  # 1 秒过期
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(q)
        return {"answer": "A"}

    async def run():
        with _approx(False):
            await cached_solve(fake_remote, "Qx", None, "explain", store=store)
            await cached_solve(fake_remote, "Qx", None, "explain", store=store)  # 仍命中
            time.sleep(1.1)
            await cached_solve(fake_remote, "Qx", None, "explain", store=store)  # 过期→未命中

    asyncio.run(run())
    assert len(calls) == 2, "TTL 过期后应重新回源 LLM"


def test_hint_level_isolation():
    """同题、mode 相同、仅 hint_level 不同 -> 两把独立键 -> 两次调用。

    回归用例：早期近似命中未按命名空间隔离，L0 的结果会被 L1 复用（跨级串味），
    本例在近似命中开启下断言仍各自回源。
    """
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(m)
        return {"answer": "A"}

    async def run():
        with _approx(True):  # 近似命中开启：同题向量相同，最易踩跨级复用
            await cached_solve(fake_remote, "Q", None, "guide", hint_level=0, store=store)
            await cached_solve(fake_remote, "Q", None, "guide", hint_level=1, store=store)

    asyncio.run(run())
    assert len(calls) == 2, "不同 Hint 级应各自回源，避免跨级串味"


def test_mode_isolation():
    """同一题、解题档（explain）与引导档（guide）不得共用缓存。"""
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(m)
        return {"answer": "A"}

    async def run():
        with _approx(True):
            await cached_solve(fake_remote, "Q", None, "explain", store=store)
            await cached_solve(fake_remote, "Q", None, "guide", store=store)

    asyncio.run(run())
    assert len(calls) == 2, "解题档与引导档应各自回源"


def test_variant_disables_approx_and_separates_key():
    """带上下文变体（Hint 历史）时：只做精确命中，不同历史各自回源。"""
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(m)
        return {"answer": "A"}

    async def run():
        with _approx(True):
            await cached_solve(
                fake_remote, "Q", None, "guide", hint_level=1,
                variant='{"history": ["a"]}', store=store,
            )
            await cached_solve(
                fake_remote, "Q", None, "guide", hint_level=1,
                variant='{"history": ["b"]}', store=store,
            )
            # 相同历史 → 精确命中，不再回源
            await cached_solve(
                fake_remote, "Q", None, "guide", hint_level=1,
                variant='{"history": ["a"]}', store=store,
            )

    asyncio.run(run())
    assert len(calls) == 2, "不同历史各自回源，相同历史命中"


# ------------------------------------------------------------- 近似命中

def test_approx_hit_only_when_question_equivalent():
    """近似命中的硬门槛（2026-10-28 事故后收紧）：只有**题干归一化后等价**才复用。

    旧契约是"语义相近就复用"，代价是 `(x+2)/e^x+a` 与 `x+2/e^x+a` 这类只差括号的
    题被串成同一份答案（实测余弦 0.988）。宁可多烧一次 LLM，也不能串答案。
    """
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(q)
        return {"answer": "X"}

    async def run():
        with _approx(True):  # 固定向量 → 余弦恒为 1.0
            # 等价写法：只差空格与全角上标（NFKC 后 ² → 2）
            await cached_solve(fake_remote, "求 f(x)=x^2 的导数", None, "explain", store=store)
            await cached_solve(fake_remote, "求f(x)=x²的导数", None, "explain", store=store)
            assert len(calls) == 1, "等价写法应命中缓存"
            # 换说法：语义相同但字面不同 → 不再复用（新契约）
            await cached_solve(fake_remote, "对 f(x)=x^2 求导", None, "explain", store=store)
            assert len(calls) == 2, "换说法不再复用，避免数学题串答案"

    asyncio.run(run())


def test_approx_miss_when_dissimilar():
    """语义无关问题（向量正交）不应近似命中。"""
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(q)
        return {"answer": "X"}

    # 两个正交向量：仅一个维度为 1，位置由问题文本长度决定
    orth_a = [1.0] + [0.0] * 63
    orth_b = [0.0] * 10 + [1.0] + [0.0] * 53

    async def run():
        with _approx(True, vectors=[orth_a]):
            await cached_solve(fake_remote, "完全无关题一", None, "explain", store=store)
        with _approx(True, vectors=[orth_b]):
            await cached_solve(fake_remote, "另一个完全无关题二", None, "explain", store=store)

    asyncio.run(run())
    assert len(calls) == 2, "语义无关题不应近似命中"


def test_approx_never_used_with_variant():
    """带变体时完全不触发近似检索（近似只服务无上下文依赖的请求）。"""
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(q)
        return {"answer": "X"}

    async def run():
        with _approx(True):  # 向量恒相同：若走近似必命中
            await cached_solve(fake_remote, "题A", None, "guide", hint_level=0, store=store)
            await cached_solve(
                fake_remote, "题B（近似题）", None, "guide", hint_level=0,
                variant='{"history": []}', store=store,
            )

    asyncio.run(run())
    assert len(calls) == 2, "带上下文变体时不得走近似复用"


# --------------------------------------------------- 失败结果不落缓存

def test_failed_remote_not_cached():
    """remote_fn 返回 None（如 Hint 生成失败）不应固化进缓存。"""
    store = MemoryStore()
    calls = []

    async def fake_remote(q, ctx, m):
        calls.append(q)
        return None  # 模拟生成失败

    async def run():
        with _approx(False):
            await cached_solve(fake_remote, "Qn", None, "guide", store=store)
            await cached_solve(fake_remote, "Qn", None, "guide", store=store)

    asyncio.run(run())
    assert len(calls) == 2, "失败结果不应缓存，每次都应回源重试"
