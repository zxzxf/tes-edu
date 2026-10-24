"""T-D3 降级标记 + 用量记录 + 预算告警测试（故障注入，不连外部依赖）。

覆盖：
- 远程调用失败（403 配额 / 超时）→ 模板响应且 ``degraded=true``；
- Hint 生成失败与契约违约 → 模板回落并标 degraded；
- 变式题失败 → ``similar_degraded=true``，主解 degraded 不受污染；
- 用量记录字段与成本口径（缓存命中成本为 0）；
- 预算判定四态（ok/warn/over/disabled）与 force_simple 动作。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.core import config, llm, socratic, usage

# ---------------------------------------------------------------------------
# 假 OpenAI 客户端：按 behavior 抛错或返回固定响应
# ---------------------------------------------------------------------------
class _FakeCompletions:
    def __init__(self, behavior: str, content: str = ""):
        self.behavior = behavior
        self.content = content

    async def create(self, **kwargs):
        if self.behavior == "quota":
            raise RuntimeError("Error code: 403 - Free quota exhausted")
        if self.behavior == "timeout":
            raise TimeoutError("Request timed out.")
        if self.behavior == "boom":
            raise ValueError("something else")
        text = self.content or '{"answer": "A", "steps": ["s1"]}'
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )


class _FakeClient:
    def __init__(self, behavior: str, content: str = ""):
        self.chat = SimpleNamespace(completions=_FakeCompletions(behavior, content))

    def __call__(self, *a, **k):  # 兼容 AsyncOpenAI(...) 被当作类调用
        return self


@pytest.fixture
def llm_on(monkeypatch):
    """打开真实 LLM 开关（不真连：AsyncOpenAI 被假客户端替换）。"""
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://fake/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3.8-flash")
    return config


@pytest.fixture
def usage_on(monkeypatch):
    """打开用量开关并收集后台记录（替换 record_usage_bg 为同步采集）。

    同时把 ``daily_cost`` 打成 0：否则预算检查会真的去连 106（单测环境 DB_DSN
    指向生产库），既慢又会留下已关闭的 asyncpg 池污染后续用例。
    """
    collected: list[dict] = []
    monkeypatch.setattr(config, "USAGE_ENABLED", True)

    async def _zero_cost(*a, **k):
        return 0.0

    monkeypatch.setattr(usage, "daily_cost", _zero_cost)

    def _bg(**kwargs):
        collected.append(usage.build_usage_row(**kwargs))

    monkeypatch.setattr(usage, "record_usage_bg", _bg)
    return collected


def _fake_openai(monkeypatch, behavior: str, content: str = ""):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda *a, **k: _FakeClient(behavior, content))


# ---------------------------------------------------------------------------
# 1) 主解降级：403 / 超时
# ---------------------------------------------------------------------------
def test_solve_degraded_on_quota_error(llm_on, usage_on, monkeypatch):
    _fake_openai(monkeypatch, "quota")
    out = asyncio.run(llm.solve("求 1+1", context={"chunks": []}, mode="explain"))
    assert out["degraded"] is True
    assert out["degraded_reason"] == "llm_unavailable"
    assert "[降级回退]" in out["answer"]
    row = usage_on[-1]
    assert row["degraded"] is True and row["error_kind"] == "quota"
    assert row["mode"] == "explain" and row["model"] == "qwen3.8-flash"


def test_solve_degraded_on_timeout(llm_on, usage_on, monkeypatch):
    _fake_openai(monkeypatch, "timeout")
    out = asyncio.run(llm.solve("求 1+1", context={"chunks": []}, mode="quick"))
    assert out["degraded"] is True
    assert usage_on[-1]["error_kind"] == "timeout"


def test_solve_success_not_degraded_and_records_tokens(llm_on, usage_on, monkeypatch):
    _fake_openai(monkeypatch, "ok")
    out = asyncio.run(llm.solve("求 1+1", context={"chunks": []}, mode="explain"))
    assert out["answer"] == "A"
    assert "degraded" not in out
    row = usage_on[-1]
    assert row["degraded"] is False and row["error_kind"] is None
    assert row["prompt_tokens"] == 100 and row["completion_tokens"] == 50
    assert row["latency_ms"] >= 0


def test_degraded_result_is_not_cached(llm_on, usage_on, monkeypatch):
    """降级结果不得进缓存：否则配额恢复后仍会连吐 7 天模板。"""
    from app.core import llm_cache

    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    _fake_openai(monkeypatch, "quota")
    store = llm_cache.MemoryStore()
    key = llm_cache.cache_key(
        subject=None, mode="explain", hint_level=None, question="题A"
    )

    async def _main():
        out = await llm.solve("题A", {"chunks": []}, mode="explain", store=store)
        return out, await store.get(key)

    out, cached = asyncio.run(_main())
    assert out["degraded"] is True
    assert cached is None


def test_dev_mode_has_no_degraded_flag(monkeypatch, usage_on):
    """未配 LLM（dev 模板模式）不算降级，响应不带 degraded。"""
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    out = asyncio.run(llm.solve("求 1+1", context={"chunks": []}, mode="explain"))
    assert "degraded" not in out


# ---------------------------------------------------------------------------
# 2) 变式题降级（旁路能力，不得污染主解 degraded）
# ---------------------------------------------------------------------------
def test_similar_degraded_on_failure(llm_on, usage_on, monkeypatch):
    _fake_openai(monkeypatch, "quota")
    out = asyncio.run(llm.suggest_similar_detailed("求 1+1", {"chunks": []}))
    assert out["degraded"] is True
    assert out["similar"]  # 仍有兜底内容
    assert usage_on[-1]["mode"] == "similar" and usage_on[-1]["degraded"] is True


def test_similar_ok_not_degraded(llm_on, usage_on, monkeypatch):
    _fake_openai(monkeypatch, "ok", content='{"similar": ["变1", "变2", "变3"]}')
    out = asyncio.run(llm.suggest_similar_detailed("求 1+1", {"chunks": []}))
    assert out["degraded"] is False
    assert out["similar"] == ["变1", "变2", "变3"]


def test_similar_dev_mode_not_degraded(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    out = asyncio.run(llm.suggest_similar_detailed("求 1+1", {"chunks": []}))
    assert out["degraded"] is False


# ---------------------------------------------------------------------------
# 3) Hint 降级：LLM 失败 / 契约违约
# ---------------------------------------------------------------------------
def test_hint_degraded_when_llm_fails(llm_on, usage_on, monkeypatch):
    _fake_openai(monkeypatch, "quota")
    hint = asyncio.run(socratic.generate_hint("求 1+1", {"chunks": []}, socratic.LEVEL_L0))
    assert hint["degraded"] is True
    assert hint["degraded_reason"] == "llm_unavailable"
    # 模板必须仍然满足"禁止空反问"契约
    assert socratic.hint_contract_violations(hint["answer"], socratic.LEVEL_L0) == []
    assert usage_on[-1]["degraded"] is True
    assert usage_on[-1]["mode"] == "guide"


def test_hint_degraded_on_contract_violation(llm_on, usage_on, monkeypatch):
    # LLM 返回一条纯问句（空反问）→ 契约违约 → 回落模板并标记
    _fake_openai(monkeypatch, "ok", content='{"answer": "你觉得呢？", "steps": []}')
    hint = asyncio.run(socratic.generate_hint("求 1+1", {"chunks": []}, socratic.LEVEL_L1))
    assert hint["degraded"] is True
    assert hint["degraded_reason"] == "contract_violation"
    assert socratic.hint_contract_violations(hint["answer"], socratic.LEVEL_L1) == []


def test_hint_dev_mode_not_degraded(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    hint = asyncio.run(socratic.generate_hint("求 1+1", {"chunks": []}, socratic.LEVEL_L0))
    assert "degraded" not in hint


# ---------------------------------------------------------------------------
# 4) 用量记录：字段 / 成本 / 缓存命中
# ---------------------------------------------------------------------------
def test_build_usage_row_cache_hit_cost_zero(monkeypatch):
    monkeypatch.setattr(config, "USAGE_ENABLED", True)
    row = usage.build_usage_row(model="m", prompt_tokens=1000, completion_tokens=1000, cache_hit=True)
    assert row["cost"] == 0.0
    assert row["cache_hit"] is True


def test_estimate_cost_uses_model_price(monkeypatch):
    monkeypatch.setattr(
        config, "LLM_MODEL_PRICES_JSON", '{"flash": {"in": 0.001, "out": 0.003}}'
    )
    # 1000 in + 1000 out → 0.001 + 0.003 = 0.004
    assert usage.estimate_cost("flash", 1000, 1000) == pytest.approx(0.004)
    # 未登记模型走默认价（0.002 / 0.006）→ 0.008
    assert usage.estimate_cost("unknown-model", 1000, 1000) == pytest.approx(0.008)


def test_record_usage_best_effort_with_recorder(monkeypatch):
    monkeypatch.setattr(config, "USAGE_ENABLED", True)
    monkeypatch.setattr(config, "DB_DSN", "")  # 无库：走 recorder 分支
    rows: list[dict] = []

    async def _rec(row):
        rows.append(row)

    got = asyncio.run(
        usage.record_usage(model="m", mode="explain", degraded=True, recorder=_rec)
    )
    assert got["degraded"] is True
    assert rows and rows[0]["degraded"] is True


def test_record_usage_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "USAGE_ENABLED", False)
    assert asyncio.run(usage.record_usage(model="m")) == {}


def test_cache_hit_callback_records_usage(monkeypatch):
    """缓存命中不烧 LLM，但要记一行 cache_hit（否则算不出缓存省了多少）。"""
    from app.core import llm_cache

    monkeypatch.setattr(config, "USAGE_ENABLED", True)
    monkeypatch.setattr(config, "DB_DSN", "")
    hits: list[str] = []

    async def _remote(q, c, m):
        raise AssertionError("命中缓存时不得回源 LLM")

    async def _main():
        store = llm_cache.MemoryStore()
        key = llm_cache.cache_key(
            subject="数学", mode="explain", hint_level=None, question="q"
        )
        await store.set(key, {"answer": "cached"})
        return await llm_cache.cached_solve(
            _remote,
            "q",
            None,
            "explain",
            subject="数学",
            store=store,
            on_hit=lambda: hits.append("hit"),
        )

    out = asyncio.run(_main())
    assert out == {"answer": "cached"}
    assert hits == ["hit"]  # 命中回调被调用（生产用它记 cache_hit 用量行）

    rows: list[dict] = []

    async def _rec(row):
        rows.append(row)

    asyncio.run(
        usage.record_usage(cache_hit=True, mode="explain", subject="数学", recorder=_rec)
    )
    assert rows and rows[0]["cache_hit"] is True and rows[0]["cost"] == 0.0


# ---------------------------------------------------------------------------
# 5) 预算判定
# ---------------------------------------------------------------------------
def test_budget_states(monkeypatch):
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET", 100.0)
    monkeypatch.setattr(config, "LLM_BUDGET_WARN_RATIO", 0.8)
    monkeypatch.setattr(config, "LLM_BUDGET_ACTION", "alert")

    st = usage.evaluate_budget(50.0)
    assert st["state"] == "ok" and st["ratio"] == 0.5 and st["force_simple"] is False

    st = usage.evaluate_budget(85.0)
    assert st["state"] == "warn" and st["force_simple"] is False

    st = usage.evaluate_budget(100.0)
    assert st["state"] == "over"
    assert st["action"] == "alert" and st["force_simple"] is False

    st = usage.evaluate_budget(120.0, action="force_simple")
    assert st["state"] == "over" and st["force_simple"] is True

    st = usage.evaluate_budget(10.0, budget=0)
    assert st["state"] == "disabled" and st["ratio"] is None


def test_classify_error():
    assert usage.classify_error(RuntimeError("403 Free quota exhausted")) == "quota"
    assert usage.classify_error(RuntimeError("429 rate limit")) == "quota"
    assert usage.classify_error(TimeoutError("timed out")) == "timeout"
    assert usage.classify_error(ValueError("boom")) == "other"


def test_force_simple_downgrades_hard_route(llm_on, usage_on, monkeypatch):
    """预算超线（action=force_simple）→ 难题档被强制换成小模型。"""
    monkeypatch.setattr(config, "LLM_MODEL_SIMPLE", "small-model")
    monkeypatch.setattr(config, "LLM_MODEL_HARD", "big-model")
    monkeypatch.setattr(usage, "budget_state", lambda: asyncio.sleep(0, result={
        "state": "over", "action": "force_simple", "force_simple": True,
        "budget": 1.0, "used": 2.0, "ratio": 2.0,
    }))
    used_models: list[str] = []
    import openai

    def _factory(*a, **k):
        client = _FakeClient("ok")

        async def _create(**kw):
            used_models.append(kw.get("model"))
            return await _FakeCompletions("ok").create(**kw)

        client.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))
        return client

    monkeypatch.setattr(openai, "AsyncOpenAI", _factory)

    asyncio.run(
        llm._remote_solve("压轴题", {"chunks": []}, "explain", tier="finale", steps=4)
    )
    # 难题档本该走 big-model，预算超线后被强制降级为小模型
    assert used_models == ["small-model"]
    assert usage_on[-1]["grade"] == "hard_forced_simple"
