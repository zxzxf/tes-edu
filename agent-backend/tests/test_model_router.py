"""T-D2 模型路由分级：路由纯函数单测（六类组合 → 期望模型）。

验收对应（计划书）：
- 路由纯函数单测（六类组合 → 期望模型）；
- ``.env`` 缺配时回落单一 ``LLM_MODEL`` 不启动失败；
- 与 T-D1 衔接：缓存未命中才进路由（本模块只决定模型名，调用方是缓存包住的远端函数）。

隔离约定：不连网络；分级模型用 monkeypatch 注入 ``config.LLM_MODEL_SIMPLE/HARD``。
"""
import pytest

from app.core import config, model_router, qtype

SIMPLE = "test-simple-model"
HARD = "test-hard-model"
SINGLE = "test-single-model"

_CTX_MEMORY = {"chunks": [{"type": "单选", "difficulty": 3}]}
_CTX_PROCESS = {"chunks": [{"type": "解答", "difficulty": 5}]}


@pytest.fixture
def routed(monkeypatch):
    """启用分级：小/大模型配成不同值。"""
    monkeypatch.setattr(config, "LLM_MODEL", SINGLE)
    monkeypatch.setattr(config, "LLM_MODEL_SIMPLE", SIMPLE)
    monkeypatch.setattr(config, "LLM_MODEL_HARD", HARD)


@pytest.fixture
def unrouted(monkeypatch):
    """未启用分级（.env 缺配）：SIMPLE/HARD 都回落单一 LLM_MODEL。"""
    monkeypatch.setattr(config, "LLM_MODEL", SINGLE)
    monkeypatch.setattr(config, "LLM_MODEL_SIMPLE", SINGLE)
    monkeypatch.setattr(config, "LLM_MODEL_HARD", SINGLE)


# --------------------------------------------------------- 六类组合（验收）

def test_01_quick_mode_uses_simple(routed):
    r = model_router.route_model(mode="quick")
    assert r["model"] == SIMPLE
    assert r["grade"] == "simple"


def test_02_similar_mode_uses_simple(routed):
    r = model_router.route_model(mode="similar")
    assert r["model"] == SIMPLE
    assert r["grade"] == "simple"


def test_03_single_choice_uses_simple(routed):
    r = model_router.route_model(mode="explain", context=_CTX_MEMORY)
    assert r["model"] == SIMPLE
    assert r["grade"] == "simple"
    assert "单选" in r["reason"]


def test_04_multi_choice_uses_simple(routed):
    r = model_router.route_model(mode="explain", qtype_="多选")
    assert r["model"] == SIMPLE
    assert r["grade"] == "simple"


def test_05_finale_uses_hard(routed):
    """压轴解答（题型过程类 + ≥3 步 + 难度 hard）-> 大模型。"""
    r = model_router.route_model(
        mode="guide", tier=qtype.TIER_FINALE, qtype_="解答", steps=4
    )
    assert r["model"] == HARD
    assert r["grade"] == "hard"


def test_06_explain_with_3plus_steps_uses_hard(routed):
    r = model_router.route_model(mode="explain", qtype_="解答", steps=3)
    assert r["model"] == HARD
    assert r["grade"] == "hard"
    assert "步" in r["reason"]


# ------------------------------------------------------------ 其余组合补充

def test_explain_under_3_steps_uses_simple(routed):
    """讲解档但步骤 <3 -> 小模型（不浪费大模型）。"""
    r = model_router.route_model(mode="explain", qtype_="解答", steps=2)
    assert r["model"] == SIMPLE


def test_guide_mode_defaults_to_simple(routed):
    """引导档（Hint）默认小模型——输出短、轮次多，成本敏感。"""
    r = model_router.route_model(mode="guide", tier=qtype.TIER_MEDIUM, qtype_="解答", steps=1)
    assert r["model"] == SIMPLE


def test_finale_hint_uses_hard(routed):
    """压轴题的分步 Hint 上大模型（推理要求高）。"""
    r = model_router.route_model(mode="guide", tier=qtype.TIER_FINALE, qtype_="解答", steps=3)
    assert r["model"] == HARD


def test_light_mode_wins_over_finale(routed):
    """轻量档（quick/similar）优先级最高——即便压轴题的直给也走小模型。"""
    r = model_router.route_model(mode="quick", tier=qtype.TIER_FINALE, steps=5)
    assert r["model"] == SIMPLE


# ------------------------------------------------- 缺配回落（不启动失败）

def test_missing_config_falls_back_to_single_model(unrouted):
    """.env 缺配 LLM_MODEL_SIMPLE/HARD：回落单一 LLM_MODEL，grade=single。"""
    for kw in (
        {"mode": "quick"},
        {"mode": "similar"},
        {"mode": "explain", "qtype_": "单选"},
        {"mode": "explain", "qtype_": "解答", "steps": 5},
        {"mode": "guide", "tier": qtype.TIER_FINALE},
    ):
        r = model_router.route_model(**kw)
        assert r["model"] == SINGLE, kw
        assert r["grade"] == "single"


def test_routing_enabled_flag(routed):
    assert config.routing_enabled() is True


def test_routing_disabled_flag(unrouted):
    assert config.routing_enabled() is False


# -------------------------------------------- 与 T-D1 衔接：命中不进路由

def test_route_is_pure_and_deterministic(routed):
    """纯函数：同入参必同出参（可缓存/可回放，便于日志核对）。"""
    a = model_router.route_model(mode="explain", context=_CTX_PROCESS, steps=3)
    b = model_router.route_model(mode="explain", context=_CTX_PROCESS, steps=3)
    assert a == b


def test_route_derives_tier_from_context_when_not_given(routed):
    """未显式传 tier 时从检索上下文推导（含难度归一化：整数 5 -> hard）。"""
    r = model_router.route_model(mode="guide", context=_CTX_PROCESS, steps=4)
    assert r["tier"] is not None
    assert r["qtype"] == "解答"


# ------------------------------------- 采样核对（验收：20 请求选择正确率 100%）

#: 采样表：(mode, qtype, steps, tier, 期望档位)。覆盖直给/变式/记忆型/压轴/多步讲解/引导档
#: 等真实流量形态——线上"采样 20 请求核对日志"的可回放替身（试点流量到位后按此表核对）。
_SAMPLES = [
    ("quick", "解答", 4, qtype.TIER_FINALE, "simple"),    # 直给档：再难也走小模型
    ("similar", "解答", 4, qtype.TIER_FINALE, "simple"),  # 变式题：轻量
    ("explain", "单选", 0, qtype.TIER_MEMORY, "simple"),  # 记忆型
    ("explain", "多选", 0, qtype.TIER_MEMORY, "simple"),
    ("guide", "单选", 0, qtype.TIER_MEMORY, "simple"),
    ("guide", "多选", 0, qtype.TIER_MEMORY, "simple"),
    ("explain", "解答", 5, qtype.TIER_FINALE, "hard"),    # 压轴讲解
    ("explain", "解答", 4, qtype.TIER_FINALE, "hard"),
    ("guide", "解答", 4, qtype.TIER_FINALE, "hard"),      # 压轴分步 Hint
    ("guide", "实验", 3, qtype.TIER_FINALE, "hard"),
    ("explain", "解答", 3, qtype.TIER_MEDIUM, "hard"),    # 讲解档 ≥3 步
    ("explain", "填空", 3, qtype.TIER_MEDIUM, "hard"),
    ("explain", "解答", 2, qtype.TIER_MEDIUM, "simple"),  # 讲解档 <3 步
    ("explain", "解答", 1, qtype.TIER_MEDIUM, "simple"),
    ("explain", "填空", 0, qtype.TIER_MEDIUM, "simple"),
    ("guide", "解答", 2, qtype.TIER_MEDIUM, "simple"),    # 引导档（Hint）默认小模型
    ("guide", "填空", 1, qtype.TIER_MEDIUM, "simple"),
    ("guide", "解答", 0, qtype.TIER_MEDIUM, "simple"),
    ("quick", "填空", 0, qtype.TIER_MEDIUM, "simple"),
    ("similar", "单选", 0, qtype.TIER_MEMORY, "simple"),
]


@pytest.mark.parametrize("mode,qtype_v,steps,tier,expected", _SAMPLES)
def test_sample_20_requests_route_correctly(routed, mode, qtype_v, steps, tier, expected):
    """采样 20 组真实流量形态，路由档位正确率须 100%。"""
    r = model_router.route_model(mode=mode, qtype_=qtype_v, steps=steps, tier=tier)
    assert r["grade"] == expected, (mode, qtype_v, steps, tier, r["reason"])
    assert r["model"] == (HARD if expected == "hard" else SIMPLE)


def test_sample_table_has_20_rows():
    """采样表规模守卫：验收口径是 20 请求。"""
    assert len(_SAMPLES) == 20
