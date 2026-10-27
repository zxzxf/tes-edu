"""T-C3 题型路由 + 分步拆解 + "直接看解析"：纯函数 / 模板 / 接口级。

验收对应（计划书）：
- 路由规则单测：四类题型 -> 期望轮次上限（记忆 1 / 中等 2 / 压轴 4）；
- 压轴题分步引导按 course_kb_steps 顺序推进（一步一 Hint）；
- 服务端 mode=direct 直达 L4（"直接看解析"一等功能）；
- 埋点事件含 direct_reveal 类型（solve.py 发射，响应字段可断言）。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core import llm, orchestrator, qtype, rag, socratic
import main

# ---------------------------------------------------------------- 构造数据

# 记忆型：单选（线上 difficulty 是整数 1-5）
_CTX_CHOICE = {
    "chunks": [
        {
            "text": "单选：集合交集运算，注意端点开闭",
            "knowledge_point": "集合运算",
            "difficulty": "3",
            "content_hash": "h-choice",
            "type": "单选",
        }
    ],
    "points": ["集合运算"],
}

# 中等：填空，步骤少
_CTX_BLANK = {
    "chunks": [
        {
            "text": "填空：函数定义域求法",
            "knowledge_point": "函数定义域",
            "difficulty": "2",
            "content_hash": "h-blank",
            "type": "填空",
        }
    ],
    "points": ["函数定义域"],
}

# 压轴：解答 + 整数难度 4（线上口径）+ ≥3 步（步骤数由 bundle 提供）
_CTX_FINALE = {
    "chunks": [
        {
            "text": "解答：二次函数与几何综合，先设参列方程组",
            "knowledge_point": "二次函数综合",
            "difficulty": "4",
            "content_hash": "h-fin",
            "type": "解答",
        }
    ],
    "points": ["二次函数综合"],
}

# 压轴题 bundle（judge_v2.load_bundle 产物形态：type + difficulty + steps）
_FINALE_BUNDLE = {
    "content_hash": "h-fin",
    "type": "解答",
    "answer": "x=-3",
    "stem": "已知二次函数 f(x)=x²-2x-3，与直线 y=kx 交于 A、B……求 k 取值范围。",
    "analysis": "设参列方程组，由判别式大于 0 得 k 的范围，再验证端点。",
    "difficulty": 4,
    "options": {},
    "steps": [
        {
            "step_no": 1,
            "step_kind": "procedure",
            "label": "(1)",
            "text": "联立方程 x²-2x-3=kx，整理为 x²-(2+k)x-3=0",
            "is_correct": None,
            "explanation": "交点即两式相等，移项整理成一元二次方程标准式",
            "error_reason": "",
            "distractor_map": None,
            "wrong_value_map": None,
            "typical_errors": None,
        },
        {
            "step_no": 2,
            "step_kind": "procedure",
            "label": "(2)",
            "text": "由两交点不同可得判别式 Δ=(2+k)²+12>0",
            "is_correct": None,
            "explanation": "两个不同交点对应方程有两个不等实根，判别式大于零",
            "error_reason": "",
            "distractor_map": None,
            "wrong_value_map": None,
            "typical_errors": None,
        },
        {
            "step_no": 3,
            "step_kind": "procedure",
            "label": "(3)",
            "text": "解不等式 (2+k)²+12>0 得 k 的取值范围",
            "is_correct": None,
            "explanation": "平方项非负再加正数，恒成立，注意化简步骤",
            "error_reason": "",
            "distractor_map": None,
            "wrong_value_map": None,
            "typical_errors": None,
        },
        {
            "step_no": 4,
            "step_kind": "procedure",
            "label": "(4)",
            "text": "验证端点并写出最终结论",
            "is_correct": None,
            "explanation": "把边界值代回原方程检查是否取等，结论要写区间形式",
            "error_reason": "",
            "distractor_map": None,
            "wrong_value_map": None,
            "typical_errors": None,
        },
    ],
}


def _bundle_of(steps: list[dict], qtype_name: str = "解答", difficulty=3) -> dict:
    b = dict(_FINALE_BUNDLE)
    b["type"] = qtype_name
    b["difficulty"] = difficulty
    b["steps"] = steps
    return b


# ---------------------------------------------------------------- 难度归一化


def test_difficulty_is_hard_normalizes_all_forms():
    # 线上整数口径（course_kb.difficulty 1-5，4/5=hard）
    assert qtype.difficulty_is_hard(4) is True
    assert qtype.difficulty_is_hard(5) is True
    assert qtype.difficulty_is_hard(3) is False
    assert qtype.difficulty_is_hard(1) is False
    # 字符串整数（embeddings.difficulty 是 text）
    assert qtype.difficulty_is_hard("4") is True
    assert qtype.difficulty_is_hard("5") is True
    assert qtype.difficulty_is_hard("3") is False
    # 旧种子/CSV 的字符串形态
    assert qtype.difficulty_is_hard("hard") is True
    assert qtype.difficulty_is_hard("困难") is True
    assert qtype.difficulty_is_hard("基础") is False
    assert qtype.difficulty_is_hard("中档") is False
    assert qtype.difficulty_is_hard("medium") is False
    # 空值/bool 守卫（bool 是 int 子类，True 不得算 hard）
    assert qtype.difficulty_is_hard(None) is False
    assert qtype.difficulty_is_hard("") is False
    assert qtype.difficulty_is_hard(True) is False


def test_is_finale_question_works_on_live_int_difficulty():
    """回归（线上数据事故）：T-C2 只认 'hard' 字符串，整数 1-5 下永不命中。"""
    assert socratic.is_finale_question(_CTX_FINALE) is True   # difficulty="4"
    assert socratic.is_finale_question(_CTX_BLANK) is False   # difficulty="2"
    assert socratic.is_finale_question({"chunks": [], "points": []}) is False
    # 字符串形态仍然兼容（旧测试口径）
    assert socratic.is_finale_question({"chunks": [{"difficulty": "hard"}]}) is True


# ---------------------------------------------------------------- 题型路由


def test_route_memory_types_direct_explain():
    for qtype_name in ("单选", "多选", "选择"):
        ctx = {"chunks": [{**_CTX_CHOICE["chunks"][0], "type": qtype_name}]}
        route = qtype.route_question_tier(ctx)
        assert route["tier"] == qtype.TIER_MEMORY
        assert route["tier_name"] == "记忆型"
        assert route["max_rounds"] == 1
        assert route["step_guided"] is False


def test_route_memory_type_beats_hard_difficulty():
    """再难的选择题也是记忆型（题型优先于难度）——不值得阶梯绕弯。"""
    ctx = {"chunks": [{**_CTX_CHOICE["chunks"][0], "difficulty": "5"}]}
    route = qtype.route_question_tier(ctx)
    assert route["tier"] == qtype.TIER_MEMORY
    assert route["max_rounds"] == 1


def test_route_blank_is_medium():
    route = qtype.route_question_tier(_CTX_BLANK)
    assert route["tier"] == qtype.TIER_MEDIUM
    assert route["max_rounds"] == 2


def test_route_finale_requires_steps_and_hard():
    # 解答 + ≥3 步 + hard -> 压轴
    route = qtype.route_question_tier(_CTX_FINALE, _FINALE_BUNDLE)
    assert route["tier"] == qtype.TIER_FINALE
    assert route["tier_name"] == "压轴"
    assert route["max_rounds"] == 4
    assert route["step_guided"] is True
    assert "4 步" in route["reason"]
    # 步骤不足 3 -> 中等（哪怕 hard）
    route2 = qtype.route_question_tier(
        _CTX_FINALE, _bundle_of(_FINALE_BUNDLE["steps"][:2], difficulty=5)
    )
    assert route2["tier"] == qtype.TIER_MEDIUM
    # 难度不够 -> 中等（注意：难度信号 chunk/bundle 取或，这里两者都降到 3）
    ctx_easy = {"chunks": [{**_CTX_FINALE["chunks"][0], "difficulty": "3"}]}
    route3 = qtype.route_question_tier(
        ctx_easy, _bundle_of(_FINALE_BUNDLE["steps"], difficulty=3)
    )
    assert route3["tier"] == qtype.TIER_MEDIUM


def test_route_without_bundle_is_conservative_medium():
    """只带 chunks（无 steps 数据）：不猜记忆型也不轻率压轴 -> 保守中等。"""
    route = qtype.route_question_tier(_CTX_FINALE)
    assert route["tier"] == qtype.TIER_MEDIUM
    assert route["step_guided"] is False
    # 完全无题型信息（dev 种子/检索未命中）同样保守中等
    route2 = qtype.route_question_tier({"chunks": [{"text": "某题"}]})
    assert route2["tier"] == qtype.TIER_MEDIUM


def test_route_bundle_type_authoritative():
    """chunks 无 type（迁移未回填）时，bundle 的 course_kb.type 兜底定档。"""
    ctx_no_type = {"chunks": [{"text": "某题", "difficulty": "3"}]}
    route = qtype.route_question_tier(ctx_no_type, _bundle_of(_FINALE_BUNDLE["steps"][:2], qtype_name="单选"))
    assert route["tier"] == qtype.TIER_MEMORY


# ---------------------------------------------------------------- 分步拆解


def test_step_guidance_returns_current_step_material():
    g = qtype.step_guidance(_FINALE_BUNDLE, 0)
    assert g["step_no"] == 1 and g["total"] == 4
    assert g["is_last"] is False
    assert "联立方程" in g["text"]
    assert g["explanation"]
    assert len(g["all_steps"]) == 4
    # 末步
    g4 = qtype.step_guidance(_FINALE_BUNDLE, 3)
    assert g4["step_no"] == 4 and g4["is_last"] is True
    # 越界收敛到末步
    assert qtype.step_guidance(_FINALE_BUNDLE, 99)["step_no"] == 4
    assert qtype.step_guidance(_FINALE_BUNDLE, -5)["step_no"] == 1


def test_step_guidance_none_when_steps_insufficient():
    bundle2 = _bundle_of(_FINALE_BUNDLE["steps"][:2])
    assert qtype.step_guidance(bundle2, 0) is None


def test_step_progress_signal():
    # 判对 -> 推进
    assert qtype.step_progress_signal({"correct": True}, None, 0) is True
    # 有实质回应 -> 推进
    assert qtype.step_progress_signal(None, "我先联立方程", 0) is True
    assert qtype.step_progress_signal({"correct": False}, "我算出来了是 -3", 0) is True
    # 挫败表达 -> 不推进（即使判错也要先停在当前步）
    assert qtype.step_progress_signal({"correct": False}, "我不懂", 1) is False
    # 空回应 -> 不推进
    assert qtype.step_progress_signal(None, "  ", 0) is False
    assert qtype.step_progress_signal(None, None, 0) is False


# ---------------------------------------------------------------- 分步模板


def test_step_templates_all_pass_contract():
    """压轴题五级分步模板必须全部过契约（含"第 N/M 步"锚点）。"""
    step = qtype.step_guidance(_FINALE_BUNDLE, 1)
    for level in range(socratic.MIN_LEVEL, socratic.MAX_LEVEL + 1):
        hint = socratic.template_hint("二次函数综合题", _CTX_FINALE, level, step)
        assert socratic.hint_contract_violations(hint["answer"], level) == [], (
            f"L{level} 分步模板违约: {hint['answer']}"
        )
        assert hint["steps"], f"L{level} 分步模板缺少步骤"
        if level < socratic.LEVEL_L4:
            assert "第 2/4 步" in hint["answer"], f"L{level} 未锚定当前步骤"
    # L4 含全步骤摘要 + 复盘三问
    hint4 = socratic.template_hint("二次函数综合题", _CTX_FINALE, 4, step)
    assert "本题共 4 步" in hint4["answer"]
    for marker in ("这题考什么", "卡在哪", "同型题"):
        assert marker in hint4["answer"]


def test_step_template_l3_withholds_final_answer():
    """分步 L3 仍不给最终答案（-3 只允许出现在答案字段素材之外的推导里）。"""
    step = qtype.step_guidance(_FINALE_BUNDLE, 1)
    hint = socratic.template_hint("二次函数综合题", _CTX_FINALE, 3, step)
    assert "最终答案先不给你" in hint["answer"]
    assert "x=-3" not in hint["answer"]


def test_generate_hint_passes_step_to_template_fallback(monkeypatch):
    """LLM 违约 -> 回落分步模板（step 参数贯通到 template_hint）。"""
    monkeypatch.setattr(socratic.config, "llm_ready", lambda: False)
    step = qtype.step_guidance(_FINALE_BUNDLE, 0)
    hint = asyncio.run(
        socratic.generate_hint("二次函数综合题", _CTX_FINALE, 1, None, step)
    )
    assert "第 1/4 步" in hint["answer"]
    assert socratic.hint_contract_violations(hint["answer"], 1) == []


def test_remote_hint_prompt_includes_step(monkeypatch):
    """LLM prompt 必须带分步锚点（防模型跳到其他步骤）。"""
    step = qtype.step_guidance(_FINALE_BUNDLE, 2)
    msgs = socratic._build_hint_messages("二次函数综合题", _CTX_FINALE, 2, None, step)
    user = msgs[1]["content"]
    assert "【分步拆解】" in user
    assert "当前第 3 步" in user
    assert "解不等式" in user  # 第 3 步的步骤文本


# ---------------------------------------------------------------- 接口级


def _patch_common(monkeypatch, retrieve):
    async def _noop_warm():
        return None

    async def _fake_similar(_q, context=None):
        return []

    monkeypatch.setattr(rag, "warm", _noop_warm)
    monkeypatch.setattr(rag, "retrieve", retrieve)
    monkeypatch.setattr(llm, "suggest_similar", _fake_similar)
    monkeypatch.setattr(socratic.config, "llm_ready", lambda: False)
    monkeypatch.setattr(socratic, "ladder", socratic.HintLadder())


@pytest.fixture
def client_finale(monkeypatch):
    """压轴题链路：检索带 type=解答/难度4，bundle 缓存返回 4 步。"""

    async def _fake_retrieve(_q, subject=None, **kwargs):
        return {"chunks": list(_CTX_FINALE["chunks"]), "points": ["二次函数综合"]}

    async def _fake_bundle(_h):
        return _FINALE_BUNDLE

    _patch_common(monkeypatch, _fake_retrieve)
    monkeypatch.setattr(orchestrator, "_load_bundle_cached", _fake_bundle)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def client_memory(monkeypatch):
    """记忆型链路：单选；bundle 缓存一旦被调用即失败（记忆型不查库）。"""

    async def _fake_retrieve(_q, subject=None, **kwargs):
        return {"chunks": list(_CTX_CHOICE["chunks"]), "points": ["集合运算"]}

    async def _boom_bundle(_h):
        raise AssertionError("记忆型不应查 bundle")

    _patch_common(monkeypatch, _fake_retrieve)
    monkeypatch.setattr(orchestrator, "_load_bundle_cached", _boom_bundle)
    with TestClient(main.app) as c:
        yield c


_H = {"X-User-Id": "stu-c3"}


def test_api_non_hint_round_carries_tier(client_memory):
    """非阶梯轮也回传题型路由（App 决定是否展示"直接看解析"按钮）。"""
    r = client_memory.post(
        "/api/app/solve", json={"question": "集合交集选择题"}, headers=_H
    )
    assert r.status_code == 200
    j = r.json()
    assert j["question_tier"] == "memory"
    assert j["question_tier_name"] == "记忆型"
    assert j["question_type"] == "单选"
    assert j["hint_max_rounds"] == 1
    assert j.get("hint_level") is None  # 非阶梯轮无阶梯字段


def test_api_memory_tier_hint_round(client_memory):
    r = client_memory.post(
        "/api/app/solve", json={"question": "集合交集选择题", "hint": True}, headers=_H
    )
    assert r.status_code == 200
    j = r.json()
    assert j["question_tier"] == "memory"
    assert j["hint_max_rounds"] == 1
    assert j["hint_finale"] is False
    assert j.get("hint_step") is None  # 非压轴无分步字段


def test_api_finale_step_guided_progression(client_finale):
    """压轴题：一步一 Hint，有实质回应推进到下一步；挫败留在当前步。"""
    q = {"question": "二次函数综合题", "hint": True}
    # 第 1 轮：L0 + 第 1 步
    r1 = client_finale.post("/api/app/solve", json=q, headers=_H)
    j1 = r1.json()
    assert j1["question_tier"] == "finale"
    assert j1["hint_finale"] is True
    assert j1["hint_max_rounds"] == 4
    assert j1["hint_step"] == 1 and j1["hint_step_total"] == 4
    assert "联立方程" in j1["hint_step_text"]
    assert "第 1/4 步" in j1["answer"]

    # 第 2 轮：实质回应 -> 推进到第 2 步（等级不因进展而升——进展走步数不走等级）
    r2 = client_finale.post(
        "/api/app/solve", json={**q, "student_reply": "我先联立两个方程"}, headers=_H
    )
    j2 = r2.json()
    assert j2["hint_step"] == 2
    assert j2["hint_level"] == 0
    assert j2["hint_frustration_streak"] == 0
    assert "第 2/4 步" in j2["answer"]
    assert "判别式" in j2["hint_step_text"]

    # 第 3 轮：挫败 1 次 -> 不跳级不推步
    r3 = client_finale.post(
        "/api/app/solve", json={**q, "student_reply": "我不懂"}, headers=_H
    )
    j3 = r3.json()
    assert j3["hint_frustration_streak"] == 1
    assert j3["hint_level"] == 0
    assert j3["hint_step"] == 2

    # 第 4 轮：挫败连击 2 -> 跳一级（L1），仍不推步
    r4 = client_finale.post(
        "/api/app/solve", json={**q, "student_reply": "还是不会"}, headers=_H
    )
    j4 = r4.json()
    assert j4["hint_frustration_streak"] == 2
    assert j4["hint_level"] == 1
    assert j4["hint_step"] == 2
    assert j4["hint_rounds"] == 4


def test_api_finale_step_caps_at_last(client_finale):
    """推步收敛到末步：连击 4 次实质回应也不会越过第 4 步。"""
    q = {"question": "二次函数综合题", "hint": True}
    client_finale.post("/api/app/solve", json=q, headers=_H)  # 第 1 步
    for _ in range(5):
        r = client_finale.post(
            "/api/app/solve", json={**q, "student_reply": "我会了，继续"}, headers=_H
        )
    j = r.json()
    assert j["hint_step"] == 4
    assert j["hint_step_total"] == 4


def test_api_mode_direct_goes_straight_to_l4(client_finale):
    """"直接看解析"一等功能：mode=direct 直达 L4（复盘三问 + 全步骤摘要）。"""
    r = client_finale.post(
        "/api/app/solve", json={"question": "二次函数综合题", "mode": "direct"}, headers=_H
    )
    assert r.status_code == 200
    j = r.json()
    assert j["hint_level"] == 4
    assert j["hint_level_name"] == "完整解析+复盘"
    assert j["hint_direct_reveal"] is True
    assert j["mode"] == "guide"
    assert j["question_tier"] == "finale"
    ans = j["answer"]
    for marker in ("这题考什么", "卡在哪", "同型题"):
        assert marker in ans
    assert "本题共 4 步" in ans  # 分步摘要（压轴 L4）
    assert socratic.hint_contract_violations(ans, 4) == []


def test_api_mode_direct_without_prior_hint_round(client_finale):
    """无历史阶梯轮时 mode=direct 同样直达 L4（首轮即点"直接看解析"）。"""
    r = client_finale.post(
        "/api/app/solve",
        json={"question": "二次函数综合题", "mode": "direct"},
        headers={"X-User-Id": "stu-fresh"},
    )
    j = r.json()
    assert j["hint_level"] == 4
    assert j["hint_rounds"] == 1


def test_api_direct_reveal_param_still_works(client_finale):
    """T-C2 的 direct_reveal 参数兼容保留（与 mode=direct 等价入口）。"""
    q = {"question": "二次函数综合题", "hint": True}
    client_finale.post("/api/app/solve", json=q, headers=_H)
    r = client_finale.post(
        "/api/app/solve", json={**q, "direct_reveal": True}, headers=_H
    )
    j = r.json()
    assert j["hint_level"] == 4
    assert j["hint_direct_reveal"] is True


def test_api_finale_judgement_advances_step(client_finale, monkeypatch):
    """提交作答且判对 -> 本轮即面向下一步（答对是最大推进信号）。"""

    async def _fake_judge(question, student_answer, context=None, **kwargs):
        return {"correct": True, "partial": False, "comment": "对", "attribution_source": "map", "need_process": False}

    monkeypatch.setattr(orchestrator.judge_v2, "judge", _fake_judge)
    q = {"question": "二次函数综合题", "hint": True}
    client_finale.post("/api/app/solve", json=q, headers=_H)  # 第 1 步
    r = client_finale.post(
        "/api/app/solve",
        json={**q, "student_answer": "x²-(2+k)x-3=0", "student_reply": "联立完了"},
        headers=_H,
    )
    j = r.json()
    assert j["judgement"]["correct"] is True
    assert j["hint_step"] == 2  # 判对：本轮 Hint 已面向第 2 步
    assert "判别式" in j["hint_step_text"]
