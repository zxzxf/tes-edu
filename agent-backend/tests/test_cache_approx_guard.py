"""2026-10-28 事故回归：近似缓存不得把"只差括号"的数学题串成同一份答案。

原题「已知 f(x)=x+2/e^x+a，f(x) 的最大值为 1」与「f(x)=(x+2)/e^x+a」的 embedding
余弦实测 0.988，而正确答案分别是"题目无解"与 a=1-e——旧阈值 0.98 下后者直接复用
了前者的结果，学生换种写法就被喂了另一道题的解析，且会固化 7 天。

这里把"题干归一化后必须等价"这道硬门槛钉死。
"""
from __future__ import annotations

import pytest

from app.core import llm_cache


def test_normalize_keeps_math_symbols():
    """归一化只该去掉空白与中文标点，斜杠/指数/括号必须原样保留。"""
    assert llm_cache.normalize_question("求 f(x)=2/e^x 的导数") == \
        llm_cache.normalize_question("求f(x)=2/e^x的导数")
    # 关键：2/e^x 绝不能被归一成 2e^x（这一步正是模型读错题干的地方）
    assert llm_cache.normalize_question("2/e^x") != llm_cache.normalize_question("2e^x")
    # 全角括号与半角括号视为同一写法，但括号的**有无**不改变等价性判断
    assert llm_cache.normalize_question("（x+2)") == llm_cache.normalize_question("(x+2)")


def test_parenthesis_position_is_not_equivalent():
    """括号位置不同 = 不同的题，必须拒绝复用。"""
    a = "已知f(x)=x+2/e^x+a, f(x)的最大值为1,则a的值为多少"
    b = "已知f(x)=(x+2)/e^x+a, f(x)的最大值为1,则a的值为多少"
    c = "已知f(x)=(x+2)/(e^x+a), f(x)的最大值为1,则a的值为多少"
    assert not llm_cache._same_question(llm_cache.normalize_question(a), b)
    assert not llm_cache._same_question(llm_cache.normalize_question(b), c)
    assert not llm_cache._same_question(llm_cache.normalize_question(a), c)


def test_content_hash_does_not_merge_different_questions():
    """content_hash 是**知识库条目**哈希，两道不同的题可能检索到同一条目——
    若只按它成键，两题就共用一份答案（本次事故真正的命中路径）。"""
    kw = dict(subject="数学", mode="guide", hint_level=4, content_hash="KB-HASH-1")
    a = llm_cache.cache_key(question="已知f(x)=x+2/e^x+a, f(x)的最大值为1", **kw)
    b = llm_cache.cache_key(question="已知f(x)=(x+2)/e^x+a, f(x)的最大值为1", **kw)
    assert a != b, "不同题干不得共用 content_hash 缓存"
    # 等价写法（只多一个空格）仍应同键，缓存才不至于形同虚设
    assert llm_cache.cache_key(question="已知f(x)=x+2/e^x+a, f(x)的最大值为1 ", **kw) == a


def test_missing_stored_question_is_rejected():
    """存侧没记题干（老数据）→ 无法证明等价 → 一律拒绝复用，不冒险。"""
    assert not llm_cache._same_question(None, "求 x^2 的导数")
    assert not llm_cache._same_question("", "求 x^2 的导数")


def test_approx_hit_requires_same_question(monkeypatch):
    """相似度达标但题干不等价时，必须回源而不是复用。"""
    import asyncio

    store = llm_cache.MemoryStore()
    q1 = "已知f(x)=x+2/e^x+a, f(x)的最大值为1,则a的值为多少"
    q2 = "已知f(x)=(x+2)/e^x+a, f(x)的最大值为1,则a的值为多少"

    calls: list[str] = []

    async def _remote(question, _ctx, _mode):
        calls.append(question)
        return {"answer": "A-" + question}

    # 假 embedding：两道题向量完全相同（模拟实测 0.988 的高相似）
    async def _fake_embed(texts, *, embed_cfg=None):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(llm_cache.embeddings, "embed", _fake_embed)
    monkeypatch.setattr(llm_cache.config, "cache_enabled", lambda: True)

    async def run():
        r1 = await llm_cache.cached_solve(_remote, q1, None, "explain", store=store)
        # 第二题：向量同样接近，但题干不等价 → 必须回源
        r2 = await llm_cache.cached_solve(_remote, q2, None, "explain", store=store)
        # 第三题：题干等价（只多个空格）→ 命中缓存，不再烧 LLM
        r3 = await llm_cache.cached_solve(_remote, q1 + " ", None, "explain", store=store)
        return r1, r2, r3

    r1, r2, r3 = asyncio.run(run())

    assert r1["answer"].startswith("A-")
    assert r2["answer"].startswith("A-") and r2["answer"] != r1["answer"]
    assert r3 == r1
    assert len(calls) == 2, "题干不同的题必须重新求解，只该复用真正等价的那次"
