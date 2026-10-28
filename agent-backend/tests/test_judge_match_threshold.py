"""判分「检索错配」红线测试（2026-10-28 线上误判事故回归）。

事故：学生输入「1+1等于」、作答「等于2」，被判 correct=False。

根因链（两层污染）：
1. ``rag.retrieve`` 按余弦排序取 TOP_K，**永远返回 K 条**——库里没有小学算术题，
   就硬凑出复数方程真题（score 仅 0.5347）；
2. ``judge_v2._pick_content_hash`` 原实现「取第一条带 hash 的 chunk」不看分数，
   于是复数题被当成"学生正在做的标注题"，判分拿着它的标准答案（C）去判「等于2」；
3. v1 兜底 ``llm._dev_judge`` / ``_remote_judge`` 同样吃未过滤的 chunks，
   且"没有参考"时返回 correct=False（把"没依据"当成"判错"）。

实测分数分布（106 生产库，2026-10-28）：
    题库真题原文自命中  0.9245 ~ 0.9879（8 题）
    库外题误配          0.3579 ~ 0.5537（6 题）
两簇之间有大片空白，故 MATCH_THRESHOLD 默认 0.75 是安全分界线。

红线：**宁可不判分（correct=None 转人工复核），也绝不用错题的答案判分。**
"""
import asyncio

import pytest

from app.core import config, judge_v2, llm, rag


def _run(coro):
    return asyncio.run(coro)


def _chunk(hash_="h-x", score=0.9, answer="C", text="某真题", **kw):
    return {"content_hash": hash_, "score": score, "answer": answer, "text": text, **kw}


def _ctx(*chunks):
    return {"chunks": list(chunks), "points": []}


# ------------------------------------------------------- rag.reliable_chunks

def test_reliable_chunks_filters_below_threshold():
    """低于门槛的 chunk 一律剔除（这就是错配进不了判分的那道闸）。"""
    ctx = _ctx(_chunk(hash_="hi", score=0.95), _chunk(hash_="lo", score=0.53))
    kept = rag.reliable_chunks(ctx)
    assert [c["content_hash"] for c in kept] == ["hi"]


def test_reliable_chunks_empty_when_all_low():
    """全部低分 -> 空列表，等价于"没检索到"。"""
    ctx = _ctx(_chunk(score=0.53), _chunk(score=0.40), _chunk(score=0.36))
    assert rag.reliable_chunks(ctx) == []


def test_reliable_chunks_treats_missing_score_as_unreliable():
    """缺 score 按 fail-closed 处理（视作不可信），不能当成高分放过。"""
    ctx = {"chunks": [{"content_hash": "h-noscore", "text": "x"}], "points": []}
    assert rag.reliable_chunks(ctx) == []


def test_reliable_chunks_handles_bad_score_types():
    """score 是脏值（None/字符串）时不得抛异常，按不可信处理。"""
    ctx = _ctx({"content_hash": "a", "score": None}, {"content_hash": "b", "score": "abc"})
    assert rag.reliable_chunks(ctx) == []


def test_reliable_chunks_tolerates_none_and_missing_key():
    """context 为 None / 没有 chunks 键都要安全返回空。"""
    assert rag.reliable_chunks(None) == []
    assert rag.reliable_chunks({}) == []
    assert rag.reliable_chunks({"chunks": None}) == []


def test_top_match_returns_highest_or_none():
    ctx = _ctx(_chunk(hash_="lo", score=0.5), _chunk(hash_="hi", score=0.9))
    assert rag.top_match(ctx)["content_hash"] == "hi"
    assert rag.top_match(_ctx(_chunk(score=0.2))) is None
    assert rag.top_match(None) is None


# -------------------------------------------------- judge_v2._pick_content_hash

def test_pick_content_hash_requires_reliable_score():
    """可信命中才给 hash；低分命中必须给 None（否则就是拿错题判分）。"""
    assert judge_v2._pick_content_hash(_ctx(_chunk(hash_="h-ok", score=0.9))) == "h-ok"
    assert judge_v2._pick_content_hash(_ctx(_chunk(hash_="h-bad", score=0.53))) is None


def test_pick_content_hash_skips_low_score_to_find_reliable():
    """低分在前、高分在后时，跳过低分取可信的那条。"""
    ctx = _ctx(_chunk(hash_="h-bad", score=0.4), _chunk(hash_="h-ok", score=0.88))
    assert judge_v2._pick_content_hash(ctx) == "h-ok"


# ---------------------------------------------- 事故场景端到端（红线用例）

def test_mismatched_low_score_chunk_is_unknown_not_wrong(monkeypatch):
    """⚠️ 事故复现：「1+1等于」错配到复数题（score 0.5347）时，
    必须返回 correct=None（转人工复核），**绝不能是 False**（把正确答案判成错误）。
    """
    complex_chunk = _chunk(
        hash_="2e4b350bf3c6", score=0.5347, answer="C",
        text="若 z/(z-1) = 1+i，则 z=（ ）",
    )
    called = []

    async def _no_llm(*_a, **_kw):
        called.append(True)
        raise AssertionError("错配题不该走到 LLM 判分（会拿复数题答案判「等于2」）")

    monkeypatch.setattr(judge_v2, "judge_by_llm", _no_llm)

    r = _run(judge_v2.judge("1+1等于", "等于2", context=_ctx(complex_chunk)))

    assert r["correct"] is None, f"正确答案被判成 {r['correct']}（事故重现）"
    assert r["attribution_source"] == "unknown"
    assert r["error_type"] == "unknown"
    assert not called, "不应调用 LLM"


def test_reliable_hit_still_judges_normally(monkeypatch):
    """对照：可信命中（真题自命中 0.92+）时判分链路照常工作，没被误伤。"""
    bundle = {
        "content_hash": "h-single", "type": "单选", "answer": "B",
        "stem": "解方程 2x=4", "analysis": "", "options": {},
        "steps": [
            {"step_no": 1, "step_kind": "option", "label": "A", "text": "x=1",
             "is_correct": False, "explanation": "", "error_reason": "",
             "distractor_map": None, "wrong_value_map": None, "typical_errors": None},
            {"step_no": 2, "step_kind": "option", "label": "B", "text": "x=2",
             "is_correct": True, "explanation": "", "error_reason": "",
             "distractor_map": None, "wrong_value_map": None, "typical_errors": None},
        ],
    }

    async def _fake_load(_h):
        return bundle

    monkeypatch.setattr(judge_v2, "load_bundle", _fake_load)

    r = _run(judge_v2.judge("解方程 2x=4", "B", context=_ctx(_chunk(hash_="h-single", score=0.97))))
    assert r["correct"] is True
    assert r["attribution_source"] == "map"


def test_threshold_boundary():
    """门槛边界：恰好等于阈值算可信，略低则不可信。"""
    th = config.MATCH_THRESHOLD
    assert judge_v2._pick_content_hash(_ctx(_chunk(hash_="eq", score=th))) == "eq"
    assert judge_v2._pick_content_hash(_ctx(_chunk(hash_="lt", score=th - 0.001))) is None


def test_threshold_is_sane():
    """阈值本身要落在实测两簇之间的空白区（0.6~0.9），防止被误配成 0/1 而失效。"""
    assert 0.6 <= config.MATCH_THRESHOLD <= 0.9


# ------------------------------------------------------- v1 兜底 _dev_judge

def test_dev_judge_no_reliable_ref_is_none_not_false():
    """⚠️ 第二层污染：无 LLM 兜底时，"没有可信参考"必须 None，不能 False。"""
    r = llm._dev_judge("等于2", _ctx(_chunk(hash_="h", score=0.4, answer="C")))
    assert r["correct"] is None, "没依据就判错会污染掌握度、误记错题"
    assert "未命中" in r["comment"]


def test_dev_judge_ignores_low_score_ref_answer():
    """兜底比对只能用可信 chunk 的答案：低分复数题答案 C 不得参与比对。"""
    low = _ctx(_chunk(hash_="h", score=0.53, answer="C"))
    assert llm._dev_judge("C", low)["correct"] is None      # 不能因为撞上错题答案而判对
    assert llm._dev_judge("等于2", low)["correct"] is None  # 也不能判错


def test_dev_judge_matches_reliable_ref():
    """对照：可信参考下比对照常生效。"""
    good = _ctx(_chunk(hash_="h", score=0.95, answer="2"))
    assert llm._dev_judge("2", good)["correct"] is True
    assert llm._dev_judge("等于2", good)["correct"] is True  # 子串包含
    assert llm._dev_judge("3", good)["correct"] is False


def test_dev_judge_empty_answer_still_false():
    """空作答仍是 False（"没交卷"和"判不了"语义不同，不要一起改成 None）。"""
    r = llm._dev_judge("   ", _ctx(_chunk(score=0.9, answer="2")))
    assert r["correct"] is False
    assert "未提交" in r["comment"]


# ---------------------------------------------------- v1 提示词与参考过滤

def test_remote_judge_prompt_drops_unreliable_refs(monkeypatch):
    """_remote_judge 拼提示词时不得混入低分 chunk 的答案（第二层污染的主路径）。"""
    seen = {}

    class _FakeMsg:
        content = '{"correct": true, "partial": false, "comment": "ok"}'

    class _FakeChoice:
        message = _FakeMsg()

    class _FakeResp:
        choices = [_FakeChoice()]

        def usage(self):
            return None

    class _FakeCompletions:
        async def create(self, **kw):
            seen["messages"] = kw["messages"]
            return _FakeResp()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    async def _fake_cfg(_tenant=None):
        return type("C", (), {"chat_model": "m", "source": "global"})()

    monkeypatch.setattr(llm.tenant_llm, "resolve_llm_config", _fake_cfg)
    monkeypatch.setattr(llm.tenant_llm, "make_client", lambda _cfg: _FakeClient())
    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    monkeypatch.setattr(llm, "_tokens", lambda _r: {})

    ctx = _ctx(_chunk(hash_="bad", score=0.53, answer="C", text="复数方程真题"))
    r = _run(llm._remote_judge("1+1等于", "等于2", ctx))

    assert r["correct"] is True  # 没有错题答案干扰后，模型能按题目本身判对
    user_prompt = seen["messages"][-1]["content"]
    assert "复数方程真题" not in user_prompt, "低分 chunk 的解析不得进提示词"
    assert "答案：C" not in user_prompt, "低分 chunk 的答案不得进提示词"


def test_judge_system_prompt_requires_self_solving():
    """提示词红线：必须先自己算出答案，且参考答案与题目不符时以自算为准。"""
    p = llm._JUDGE_SYSTEM
    assert "独立算出" in p
    assert "以你自己算出的结果为准" in p
    # 「没写过程」不得成为判错理由（1+1 这类题本就没有过程可写）
    assert "不要因为没写过程就判错" in p
    # 表述差异等价（等于2 / 2 / 答案为2）
    assert "等于2" in p


# ------------------------------------------------- 门槛可配（env 覆盖）

def test_threshold_configurable(monkeypatch):
    """MATCH_THRESHOLD 可用环境变量覆盖（不同 embedding 模型分数分布不同）。"""
    monkeypatch.setattr(config, "MATCH_THRESHOLD", 0.95)
    assert judge_v2._pick_content_hash(_ctx(_chunk(hash_="h", score=0.9))) is None
    monkeypatch.setattr(config, "MATCH_THRESHOLD", 0.3)
    assert judge_v2._pick_content_hash(_ctx(_chunk(hash_="h", score=0.4))) == "h"
