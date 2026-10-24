"""T-C1 意图三档路由：规则分类 + 兜底顺序 + /solve 接口分流。

验收点（对应计划书 T-C1）：
- 三类意图（direct/explain/guide）规则分类正确；
- 兜底顺序：规则 -> LLM -> 默认引导档，**任何失败都不静默直给**；
- /solve 不传 mode 时 llm.solve 收到的 mode 与档位一致（prompt 档位标记的来源）；
- 显式传 mode 仍被尊重（向后兼容 /similar 等内部调用）。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core import intent, llm, orchestrator, rag
import main


# ---------- 纯函数：规则分类 ----------

@pytest.mark.parametrize(
    "question",
    [
        "赶时间，直接给答案",
        "快点，直接把答案告诉我",
        "别讲了，just tell me the answer",
        "来不及了，直接说答案",
    ],
)
def test_direct_keywords(question):
    """显式"赶时间/只要答案" -> 直给档。"""
    assert intent.classify_intent(question) == intent.INTENT_DIRECT


@pytest.mark.parametrize(
    "question",
    [
        "为什么这里要变号？",
        "这道题的原理是什么",
        "帮我讲讲这个解法怎么来的",
        "没听懂，解释一下第三步",
    ],
)
def test_explain_keywords(question):
    """显式求理解（为什么/原理/讲解）-> 讲解优先档。"""
    assert intent.classify_intent(question) == intent.INTENT_EXPLAIN


@pytest.mark.parametrize(
    "question",
    [
        "这题怎么做？",
        "这道题答案是什么",
        "求解这个方程",
        "帮我做一下这道题",
    ],
)
def test_guide_keywords(question):
    """裸问"怎么做/答案" -> 引导档（要答案的学生同样先进引导）。"""
    assert intent.classify_intent(question) == intent.INTENT_GUIDE


def test_uncovered_question_returns_none():
    """规则未覆盖的问句返回 None（交由兜底链，不猜档）。"""
    assert intent.classify_intent("已知三角形三边长度，求其面积") is None
    assert intent.classify_intent("") is None
    assert intent.classify_intent("   ") is None


def test_direct_beats_explain():
    """同时出现两类信号时直给优先（学生明确放弃引导）。"""
    assert intent.classify_intent("别讲了，为什么答案是这个，直接说") == intent.INTENT_DIRECT


# ---------- 兜底链：resolve_response_mode ----------
# 约定与套件一致（无 pytest-asyncio）：async 协程用 asyncio.run 包同步断言。


def test_resolve_rules_hit_no_llm_call(monkeypatch):
    """规则命中时绝不调 LLM（零成本路径）。"""

    async def _boom(*a, **k):
        raise AssertionError("规则命中不应触发 LLM 分类")

    monkeypatch.setattr(intent, "classify_intent_llm", _boom)
    i, m, src = asyncio.run(intent.resolve_response_mode("这题怎么做"))
    assert (i, m, src) == (intent.INTENT_GUIDE, "guide", "rules")


def test_resolve_llm_fallback_used_when_rules_miss(monkeypatch):
    """规则未命中且问句够长 -> LLM 兜底生效。"""

    async def _fake_llm(q, context=None):
        return intent.INTENT_EXPLAIN

    monkeypatch.setattr(intent, "classify_intent_llm", _fake_llm)
    q = "一个三角形已知三边长度分别为 3、4、5，我想弄清楚它的面积是怎么推出来的"
    i, m, src = asyncio.run(intent.resolve_response_mode(q))
    assert (i, m, src) == (intent.INTENT_EXPLAIN, "explain", "llm")


def test_resolve_llm_failure_degrades_to_guide(monkeypatch):
    """LLM 分类失败（None）-> 显式降级引导档，不静默直给。"""

    async def _fail_llm(q, context=None):
        return None

    monkeypatch.setattr(intent, "classify_intent_llm", _fail_llm)
    q = "一个三角形已知三边长度分别为 3、4、5，我想弄清楚它的面积是怎么推出来的"
    i, m, src = asyncio.run(intent.resolve_response_mode(q))
    assert (i, m, src) == (intent.INTENT_GUIDE, "guide", "default")


def test_resolve_short_question_skips_llm(monkeypatch):
    """短问句（最常见形态）不花 LLM 调用，直接默认引导档。"""

    async def _boom(*a, **k):
        raise AssertionError("短问句不应触发 LLM 分类")

    monkeypatch.setattr(intent, "classify_intent_llm", _boom)
    i, m, src = asyncio.run(intent.resolve_response_mode("已知三边求面积"))
    assert (i, m, src) == (intent.INTENT_GUIDE, "guide", "default")


def test_llm_classifier_returns_none_without_credentials(monkeypatch):
    """无 LLM 密钥时分类器直接返回 None（不抛异常阻塞主链路）。"""
    monkeypatch.setattr(intent.config, "llm_ready", lambda: False)
    assert asyncio.run(intent.classify_intent_llm("随便一个问题")) is None


def test_llm_classifier_parses_tier(monkeypatch):
    """LLM 分类器正确解析合法回包并拒绝非法值。"""

    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Completions:
        def __init__(self, content):
            self._content = content

        async def create(self, **kwargs):
            assert kwargs["temperature"] == 0
            return _Resp(self._content)

    class _FakeClient:
        def __init__(self, content):
            self.chat = type("C", (), {"completions": _Completions(content)})()

    monkeypatch.setattr(intent.config, "llm_ready", lambda: True)

    import sys
    import types

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = lambda **k: _FakeClient("direct")
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    assert asyncio.run(intent.classify_intent_llm("问题")) == intent.INTENT_DIRECT

    # 非法回包 -> None
    fake_openai.AsyncOpenAI = lambda **k: _FakeClient("我不知道")
    assert asyncio.run(intent.classify_intent_llm("问题")) is None


# ---------- 接口级：/solve 三档分流 ----------

@pytest.fixture
def client(monkeypatch):
    """起测试客户端：屏蔽预热，mock RAG/LLM，记录 llm.solve 收到的 mode。"""
    calls = {"solve_modes": []}

    async def _noop_warm():
        return None

    async def _fake_retrieve(_q, subject=None):
        return {"chunks": [], "points": []}

    async def _fake_solve(_q, context=None, mode="explain"):
        calls["solve_modes"].append(mode)
        return {"answer": "x=2", "steps": ["移项", "求解"]}

    async def _fake_similar(_q, context=None):
        return []

    monkeypatch.setattr(rag, "warm", _noop_warm)
    monkeypatch.setattr(rag, "retrieve", _fake_retrieve)
    monkeypatch.setattr(llm, "solve", _fake_solve)
    monkeypatch.setattr(llm, "suggest_similar", _fake_similar)

    with TestClient(main.app) as c:
        c.calls = calls  # 挂载断言用
        yield c


def test_solve_without_mode_routes_to_guide(client):
    """不传 mode：裸问"怎么做" -> 引导档，llm.solve 收到 guide。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4，这题怎么做"})
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == "guide"
    assert j["mode"] == "guide"
    assert j["intent_source"] == "rules"
    assert client.calls["solve_modes"][-1] == "guide"


def test_solve_explain_intent_routes_to_explain(client):
    """问为什么 -> 讲解优先档。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4，为什么要移项"})
    assert r.json()["intent"] == "explain"
    assert r.json()["mode"] == "explain"
    assert client.calls["solve_modes"][-1] == "explain"


def test_solve_direct_intent_routes_to_quick(client):
    """赶时间 -> 简版直给档。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4，赶时间直接给答案"})
    assert r.json()["intent"] == "direct"
    assert r.json()["mode"] == "quick"
    assert client.calls["solve_modes"][-1] == "quick"


def test_solve_explicit_mode_respected(client):
    """显式传 mode 仍被尊重（向后兼容）：intent 不分类，source=explicit。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4", "mode": "explain"})
    j = r.json()
    assert j["mode"] == "explain"
    assert j["intent"] is None
    assert j["intent_source"] == "explicit"
    assert client.calls["solve_modes"][-1] == "explain"


def test_similar_endpoint_still_similar_mode(client):
    """/similar 内部调用 mode=similar 不受三档路由影响。"""
    r = client.post("/api/app/similar", json={"question": "解方程 2x=4"})
    assert r.status_code == 200
    assert client.calls["solve_modes"][-1] == "similar"
