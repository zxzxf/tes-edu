"""T-C2 Hint 阶梯 L0–L4：纯函数 / 状态机 / 契约 / 回落 / 接口级。

验收对应（计划书）：
- 五级递进、挫败跳级、L4 复盘三问必现；
- "禁止空反问"契约钉住（模板断言 + LLM 违约回落）；
- /solve hint=true 全链路（mock LLM）冒烟。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core import intent, llm, orchestrator, rag, socratic
import main

_CTX_MEDIUM = {
    "chunks": [
        {
            "text": "移项时要两边同时加减同一数量，依据是等式的基本性质",
            "knowledge_point": "一元一次方程",
            "difficulty": "medium",
            "content_hash": "h1",
        }
    ],
    "points": ["一元一次方程"],
}
_CTX_HARD = {
    "chunks": [
        {
            "text": "压轴题：二次函数与几何综合，先设参列方程组",
            "knowledge_point": "二次函数综合",
            "difficulty": "hard",
            "content_hash": "h2",
        }
    ],
    "points": ["二次函数综合"],
}


# ---------- 挫败信号 / 压轴题判定 ----------

def test_frustration_signal_detection():
    assert socratic.is_frustration_signal("我不懂") is True
    assert socratic.is_frustration_signal("还是不懂，直接说吧") is True
    assert socratic.is_frustration_signal("我觉得应该先移项") is False
    assert socratic.is_frustration_signal("") is False
    assert socratic.is_frustration_signal(None) is False


def test_finale_question_detection():
    assert socratic.is_finale_question(_CTX_HARD) is True
    assert socratic.is_finale_question(_CTX_MEDIUM) is False
    assert socratic.is_finale_question({"chunks": [], "points": []}) is False


# ---------- decide_level：五级递进 / 跳级 / 封顶 / 直达 ----------

def test_level_stays_without_signal():
    assert socratic.decide_level(0, 0) == socratic.LEVEL_L0
    assert socratic.decide_level(2, 1) == socratic.LEVEL_L2


def test_frustration_streak_jumps_one_level():
    # 连续 2 次挫败 -> 跳一级（计划书口径）
    assert socratic.decide_level(0, socratic.FRUSTRATION_JUMP) == 1
    # 单次挫败不跳
    assert socratic.decide_level(0, socratic.FRUSTRATION_JUMP - 1) == 0
    # 每轮连击达标各跳一级（真实对话中是逐轮推进，这里验证单次调用的幅度）
    assert socratic.decide_level(1, 3) == 2


def test_finale_caps_at_l3():
    # 压轴题：挫败到顶也只到 L3，L4 不能被"挫败"刷出来
    assert socratic.decide_level(2, 9, finale=True) == socratic.FINALE_CAP
    # 当前等级越界时收敛到封顶
    assert socratic.decide_level(4, 0, finale=True) == socratic.FINALE_CAP
    # 非压轴题可以到 L4
    assert socratic.decide_level(3, 9) == socratic.LEVEL_L4


def test_direct_reveal_jumps_to_l4_even_for_finale():
    lv = socratic.decide_level(0, 0, direct_reveal=True, finale=True)
    assert lv == socratic.LEVEL_L4


# ---------- "禁止空反问"契约 ----------

def test_template_hints_all_pass_contract():
    """五级模板必须全部过契约（模板断言，计划书验收点）。"""
    for level in range(socratic.MIN_LEVEL, socratic.MAX_LEVEL + 1):
        hint = socratic.template_hint("解方程 2x+6=0", _CTX_MEDIUM, level)
        assert socratic.hint_contract_violations(hint["answer"], level) == [], (
            f"L{level} 模板违约: {hint['answer']}"
        )
        assert hint["steps"], f"L{level} 模板缺少步骤"


def test_contract_rejects_question_only():
    # 空反问：整条只是一个问句 -> question_only + missing_action
    v = socratic.hint_contract_violations("你觉得这一步应该怎么做？", 1)
    assert "question_only" in v


def test_contract_rejects_missing_action():
    v = socratic.hint_contract_violations("这道题考的是一元一次方程。", 0)
    assert "missing_action" in v


def test_contract_l1_requires_direction():
    # 有动作但无方向性表述 -> L1 专项违约
    v = socratic.hint_contract_violations("下一步你把式子写出来。", 1)
    assert "missing_direction" in v
    # 补上方向性表述即通过
    ok = socratic.hint_contract_violations("通法方向是先移项，下一步你把式子写出来。", 1)
    assert ok == []


def test_contract_l4_requires_review_three_questions():
    v = socratic.hint_contract_violations("完整解析：先移项再求解，下一步你自己练一道。", 4)
    assert "missing_review" in v


def test_contract_empty_text():
    assert socratic.hint_contract_violations("   ", 0) == ["empty"]
    assert socratic.hint_contract_violations(None, 2) == ["empty"]


# ---------- 阶梯状态机（进程内） ----------

def test_ladder_default_state_and_progression():
    ld = socratic.HintLadder()
    st = ld.get("s1", "解方程 2x+6=0")
    assert st == {"level": 0, "streak": 0, "rounds": 0, "step": 0}
    ld.record_round("s1", "解方程 2x+6=0", level=1, streak=2)
    st = ld.get("s1", "解方程 2x+6=0")
    assert st["level"] == 1 and st["streak"] == 2 and st["rounds"] == 1
    ld.record_round("s1", "解方程 2x+6=0", level=1, streak=0)
    assert ld.get("s1", "解方程 2x+6=0")["rounds"] == 2


def test_ladder_step_state_persisted_and_preserved():
    """T-C3：step=None（非压轴轮）不覆盖已有分步进度；显式 step 才落库。"""
    ld = socratic.HintLadder()
    ld.record_round("s1", "压轴题", level=1, streak=0, step=2)
    assert ld.get("s1", "压轴题")["step"] == 2
    ld.record_round("s1", "压轴题", level=1, streak=0)  # 未传 step -> 保留 2
    assert ld.get("s1", "压轴题")["step"] == 2
    ld.record_round("s1", "压轴题", level=2, streak=0, step=3)
    assert ld.get("s1", "压轴题")["step"] == 3


def test_ladder_state_isolated_by_student_and_question():
    ld = socratic.HintLadder()
    ld.record_round("s1", "题A", level=3, streak=2)
    ld.record_round("s2", "题A", level=0, streak=0)
    assert ld.get("s1", "题A")["level"] == 3
    assert ld.get("s2", "题A")["level"] == 0
    # 同学生的另一道题互不干扰
    assert ld.get("s1", "题B")["level"] == 0
    # 空白差异的同一题归一为同一键
    ld.record_round("s1", "题A", level=4, streak=0)
    assert ld.get("s1", " 题A ")["level"] == 4


def test_ladder_evicts_oldest_beyond_capacity():
    ld = socratic.HintLadder(max_entries=3)
    for i in range(5):
        ld.record_round("s", f"题{i}", level=0, streak=0)
    assert len(ld._states) == 3
    # 最早的题0/题1 被淘汰（回退默认态），最新的题4 仍在
    assert ld.get("s", "题0")["rounds"] == 0
    assert ld.get("s", "题1")["rounds"] == 0
    assert ld.get("s", "题4")["rounds"] == 1


# ---------- generate_hint：LLM 优先 / 违约与失败回落模板 ----------

def test_generate_hint_falls_back_to_template_without_llm(monkeypatch):
    monkeypatch.setattr(socratic.config, "llm_ready", lambda: False)
    hint = asyncio.run(socratic.generate_hint("解方程 2x+6=0", _CTX_MEDIUM, 1))
    assert socratic.hint_contract_violations(hint["answer"], 1) == []
    assert "一元一次方程" in hint["answer"]


def test_generate_hint_uses_llm_output_when_contract_ok(monkeypatch):
    async def _fake_remote(question, context, level, history=None, step=None, **kwargs):
        return {"answer": "通法方向是先移项，下一步你把代入后的式子写出来。", "steps": ["a"]}

    monkeypatch.setattr(socratic, "_remote_hint", _fake_remote)
    hint = asyncio.run(socratic.generate_hint("解方程 2x+6=0", _CTX_MEDIUM, 1))
    assert hint["answer"].startswith("通法方向")


def test_generate_hint_rejects_contract_violating_llm_output(monkeypatch):
    """LLM 输出空反问 -> 契约拒绝 -> 回落模板（防 LLM 偷懒只提问）。"""

    async def _bad_remote(question, context, level, history=None, step=None, **kwargs):
        return {"answer": "你觉得这一步该怎么做？", "steps": []}

    monkeypatch.setattr(socratic, "_remote_hint", _bad_remote)
    hint = asyncio.run(socratic.generate_hint("解方程 2x+6=0", _CTX_MEDIUM, 1))
    assert "question_only" not in socratic.hint_contract_violations(hint["answer"], 1)
    assert "一元一次方程" in hint["answer"]  # 回落的是模板


def test_generate_hint_falls_back_when_llm_raises(monkeypatch):
    async def _boom(question, context, level, history=None, step=None, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(socratic, "_remote_hint", _boom)
    hint = asyncio.run(socratic.generate_hint("解方程 2x+6=0", _CTX_MEDIUM, 3))
    assert socratic.hint_contract_violations(hint["answer"], 3) == []


# ---------- LLM 自治降级回归（T-C2 线上实测事故） ----------

def _patch_openai_raising(monkeypatch):
    """把 openai.AsyncOpenAI 换成必然抛错的假客户端（模拟配额耗尽 403）。"""
    import sys
    import types

    async def _raise(**kwargs):
        raise RuntimeError("403 Free quota exhausted")

    fake = types.ModuleType("openai")
    fake.AsyncOpenAI = lambda **k: types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=_raise)
        )
    )
    monkeypatch.setitem(sys.modules, "openai", fake)


def test_suggest_similar_survives_quota_exhausted(monkeypatch):
    """回归：变式 LLM 403 曾把整轮 Hint 对话打成 500——similar 必须自治降级。"""
    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    _patch_openai_raising(monkeypatch)
    sims = asyncio.run(llm.suggest_similar("解方程 2x+6=0", _CTX_MEDIUM))
    assert isinstance(sims, list) and sims
    assert "一元一次方程" in sims[0]  # 检索兜底，不抛异常


def test_solve_falls_back_to_degraded_when_llm_unavailable(monkeypatch):
    """回归：主讲解 LLM 403 -> 降级为基于检索的通法（文案标明降级，非 dev 占位）。"""
    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    _patch_openai_raising(monkeypatch)
    out = asyncio.run(llm.solve("解方程 2x+6=0", _CTX_MEDIUM, "explain"))
    assert "降级回退" in out["answer"]
    assert "一元一次方程" in out["answer"]


def test_dev_solve_label_without_degraded(monkeypatch):
    """无密钥 dev 模式保持原文案（与生产降级可区分）。"""
    out = llm._dev_solve("解方程 2x+6=0", _CTX_MEDIUM, "explain")
    assert "[dev-无密钥回退]" in out["answer"]


# ---------- 接口级：/solve hint=true 全链路（mock LLM） ----------

@pytest.fixture
def client(monkeypatch):
    async def _noop_warm():
        return None

    async def _fake_retrieve(_q, subject=None, **kwargs):
        return {"chunks": list(_CTX_MEDIUM["chunks"]), "points": ["一元一次方程"]}

    async def _fake_similar(_q, context=None):
        return []

    async def _fake_solve(_q, context=None, mode="explain", **kwargs):
        # T-D1: 主链路会给 llm.solve 透传 subject/hint_level/content_hash（缓存键用）
        return {"answer": "x=-3", "steps": ["移项", "求解"]}

    monkeypatch.setattr(rag, "warm", _noop_warm)
    monkeypatch.setattr(rag, "retrieve", _fake_retrieve)
    monkeypatch.setattr(llm, "solve", _fake_solve)
    monkeypatch.setattr(llm, "suggest_similar", _fake_similar)
    # 测试环境不碰真实 LLM：Hint 走模板路径（契约由模板保证）
    monkeypatch.setattr(socratic.config, "llm_ready", lambda: False)
    # 每个用例一套干净阶梯状态（模块级单例会被用例间共享）
    monkeypatch.setattr(socratic, "ladder", socratic.HintLadder())
    with TestClient(main.app) as c:
        yield c


_H = {"X-User-Id": "stu-1"}


def test_api_hint_first_round_starts_at_l0(client):
    r = client.post("/api/app/solve", json={"question": "解方程 2x+6=0", "hint": True}, headers=_H)
    assert r.status_code == 200
    j = r.json()
    assert j["hint_level"] == 0
    assert j["hint_level_name"] == "点考点"
    assert j["mode"] == "guide"
    assert j["intent"] == "guide"
    assert j["intent_source"] == "hint_ladder"
    assert j["hint_frustration_streak"] == 0
    # L0 不给最终答案（x=-3 不得出现在首轮 Hint 里）
    assert "x=-3" not in j["answer"] and "-3" not in j["answer"]
    assert socratic.hint_contract_violations(j["answer"], 0) == []


def test_api_hint_two_frustrations_jump_one_level(client):
    q = {"question": "解方程 2x+6=0", "hint": True}
    client.post("/api/app/solve", json=q, headers=_H)  # L0
    r1 = client.post(
        "/api/app/solve", json={**q, "student_reply": "我不懂"}, headers=_H
    )  # 连击 1，不跳
    assert r1.json()["hint_frustration_streak"] == 1
    assert r1.json()["hint_level"] == 0
    r2 = client.post(
        "/api/app/solve", json={**q, "student_reply": "还是不懂，直接说吧"}, headers=_H
    )  # 连击 2，跳一级
    assert r2.json()["hint_frustration_streak"] == 2
    assert r2.json()["hint_level"] == 1
    assert r2.json()["hint_level_name"] == "给方向"


def test_api_hint_substantive_reply_resets_streak(client):
    q = {"question": "解方程 2x+6=0", "hint": True}
    client.post("/api/app/solve", json=q, headers=_H)  # L0
    client.post("/api/app/solve", json={**q, "student_reply": "我不懂"}, headers=_H)
    r = client.post(
        "/api/app/solve", json={**q, "student_reply": "我知道了，先移项"}, headers=_H
    )
    assert r.json()["hint_frustration_streak"] == 0
    assert r.json()["hint_level"] == 0
    # 清零后要重新累计两次才跳级
    client.post("/api/app/solve", json={**q, "student_reply": "我不懂"}, headers=_H)
    assert r.json()["hint_level"] == 0


def test_api_hint_direct_reveal_goes_straight_to_l4(client):
    q = {"question": "解方程 2x+6=0", "hint": True}
    client.post("/api/app/solve", json=q, headers=_H)  # L0
    r = client.post("/api/app/solve", json={**q, "direct_reveal": True}, headers=_H)
    j = r.json()
    assert j["hint_level"] == 4
    assert j["hint_direct_reveal"] is True
    # L4 必含复盘三问（计划书硬性要求）
    ans = j["answer"]
    for marker in ("这题考什么", "卡在哪", "同型题"):
        assert marker in ans
    assert socratic.hint_contract_violations(ans, 4) == []


def test_api_hint_state_isolated_per_student(client):
    q = {"question": "解方程 2x+6=0", "hint": True}
    client.post("/api/app/solve", json={**q, "direct_reveal": True}, headers=_H)
    # 另一学生首轮仍从 L0 起
    r = client.post("/api/app/solve", json=q, headers={"X-User-Id": "stu-2"})
    assert r.json()["hint_level"] == 0


def test_api_hint_off_keeps_tier_routing(client):
    """hint 不传（缺省 False）时仍是 T-C1 三档路由，不回阶梯字段语义。"""
    r = client.post(
        "/api/app/solve",
        json={"question": "解方程 2x+6=0，为什么要移项？"},
        headers=_H,
    )
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == "explain"
    assert j["intent_source"] == "rules"
    assert j.get("hint_level") is None


def test_template_evidence_is_marked_as_other_question():
    """回归：模板回落里的检索片段必须标明『非本题』，防止同类题答案被当成本题答案。

    事故背景：题库条目（f(x)=x+2e^x+a…解得 a=2）被降级文案原样吐出，
    学生在另一道题上看到 a=2。片段只可作通法参考。
    """
    ctx = {"chunks": [{"text": "已知函数 f(x)=x+2e^x+a 的最大值为 1，解得 a=2",
                       "knowledge_point": "导数与函数最值", "score": 0.9}]}
    ev = socratic._evidence(ctx)
    assert "非本题" in ev
    assert "不可直接套用" in ev

    for level in (socratic.LEVEL_L2, socratic.LEVEL_L3, socratic.LEVEL_L4):
        out = socratic.template_hint("求 a 的值", ctx, level)
        ans = out["answer"]
        if "同类题思路" in ans or "关键依据" in ans or "这一步这样推" in ans:
            assert "非本题" in ans
    # L4 必须额外给出显式告警句
    assert "当成本题答案" in socratic.template_hint("求 a 的值", ctx, socratic.LEVEL_L4)["answer"]


def test_slash_ambiguity_triggers_dual_parse_hint():
    """回归：斜杠歧义题干（x+2/e^x+a）必须触发『两种解读都给』的提示。

    真机事故：OCR 把 \frac{x+2}{e^x+a} 压成 x+2/e^x+a，a 从分母跑到分子侧，
    题目被换成另一道，答案从 a=1 变 a=2。
    """
    assert llm._slash_ambiguous("已知 f(x)=x+2/e^x+a 的最大值为 1，求 a")
    assert not llm._slash_ambiguous("已知 f(x)=\\frac{x+2}{e^x+a} 的最大值为 1，求 a")
    assert not llm._slash_ambiguous("求 x/2 的值")

    msgs = llm._build_messages("已知 f(x)=x+2/e^x+a 的最大值为 1，求 a", None, "direct")
    body = msgs[-1]["content"]
    assert "歧义" in body
    assert "分别" in body


def test_short_compute_question_goes_direct_not_guide():
    """回归：1+1 这类短计算题必须直给，不能落引导档（否则答成『用手指或画图数 1』）。"""
    assert intent._looks_like_compute("1+1等于多少")
    assert intent._looks_like_compute("求 3x+5=14 中 x 的值")
    assert not intent._looks_like_compute("这题怎么做")
    assert not intent._looks_like_compute("为什么这样解")
    assert not intent._looks_like_compute("帮我讲解一下导数")  # 无数字
    i, m, src = asyncio.run(intent.resolve_response_mode("1+1等于多少"))
    assert (i, m, src) == (intent.INTENT_DIRECT, "quick", "default")


def test_no_internal_retrieval_state_leaks_into_prompt():
    """回归：提示词不得让学生看到『检索无结果』这类系统内部状态。

    真机现象：问 1+1，答案开头是「检索无结果。依基础运算通法：1+1=2」。
    """
    # 禁止条款本身必须在（它靠列举反面话术生效，故不能按字面去搜"检索无结果"）
    assert "禁止在答案里汇报系统内部状态" in llm._SYSTEM
    # 但不得再出现要求模型"说明检索情况"的指令
    assert "请说明" not in llm._SYSTEM

    msgs = llm._build_messages("1+1等于多少", None, "quick")
    body = msgs[-1]["content"]
    assert "无检索结果" not in body
    assert "请说明" not in body
    assert "（无参考材料，直接按学科通法作答）" in body  # 只约束行为，不要求汇报


def test_thinking_disabled_by_default_can_be_enabled_per_mode():
    """回归：推理模型思维链默认必须关掉。

    实测 qwen3.6-flash 同一道导数含参题：开思考 33.8s/4299 tok（reasoning 3694），
    关思考 5.5s/600 tok，推导仍正确。思维链不进答案正文，对学生是纯等待。
    """
    kw = llm.thinking_kwargs("quick")
    assert kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False

    # 按档位单独开启时（LLM_THINKING_MODES 含该档）不再下发关闭参数
    import app.core.config as cfg
    old = cfg.LLM_THINKING_MODES
    try:
        cfg.LLM_THINKING_MODES = {"explain"}
        assert llm.thinking_kwargs("explain") == {}
        assert llm.thinking_kwargs("quick") != {}
    finally:
        cfg.LLM_THINKING_MODES = old
