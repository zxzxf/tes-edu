"""M1-8 拍照入口收敛：不在作业中的题不判分、不误导。

产品口径（方案书 M1-8 / A9）：
* 拍照只为**作业里的题**服务；识别结果匹配不到已下发题 -> 明确提示
  「本题不在你的作业中」，**不进入判分**；
* 讲解 / 图谱 / 错题集照旧可用，所以拒绝不是"功能坏了"，而是给两条出口：
  去作业列表、或只求讲解（明确不判分）。

本文件覆盖两件事：
1. 题面匹配的纯函数（OCR 会丢标点、改全半角、行内换行，必须容错）；
2. ``handle_image`` 在无匹配时的短路行为（不调解题、不调判分）。
"""
import asyncio

import pytest

from app.core import assignment_match, ocr, orchestrator, tenant_llm
from app.core.tenant_llm import LLMConfig


def _cfg(vlm_model="qwen-vl-max"):
    return LLMConfig(
        chat_base_url="https://c.example/v1", chat_api_key="sk", chat_model="qwen",
        embed_base_url="", embed_api_key="", embed_model="", embed_dim=4,
        model_simple="", model_hard="", rag_top_k=4, source="tenant",
        vlm_base_url="https://vlm.example/v1", vlm_model=vlm_model,
    )


# ---------------------------------------------------------------- 归一/匹配
@pytest.mark.parametrize(
    "a,b",
    [
        ("已知函数 f(x)=x²+1，求 f(1)。", "已知函数f(x)=x2+1,求f(1)"),
        ("A．1\u3000B．2", "A.1 B.2"),
        ("解方程：\n2x=4", "解方程: 2x=4"),
        ("$\\frac{1}{2}$ 是多少", "1/2 是多少"),
    ],
)
def test_stem_matches_ignores_formatting_noise(a, b):
    assert assignment_match.stems_match(a, b) is True


def test_stem_match_rejects_different_question():
    assert assignment_match.stems_match("求 2x=4 的解", "求 3x=9 的解") is False


def test_stem_match_rejects_empty():
    assert assignment_match.stems_match("", "2x=4") is False
    assert assignment_match.stems_match("2x=4", "  ") is False


def test_best_match_returns_seq_of_closest_question():
    questions = [
        {"seq": 1, "stem": "求 2x=4 的解"},
        {"seq": 2, "stem": "已知函数 f(x)=x²+1，求 f(1)"},
    ]
    assert assignment_match.best_match("已知函数f(x)=x2+1,求f(1)", questions) == 2
    assert assignment_match.best_match("完全不相关的题目 1+1", questions) is None


# ------------------------------------------------------------ handle_image
def test_handle_image_rejects_question_not_in_assignment(monkeypatch):
    async def fake_resolve(_t):
        return _cfg()

    async def fake_ocr(*a, **k):
        return "完全不相干的题：1+1 等于几"

    async def fake_lookup(**_kw):
        return [{"seq": 1, "stem": "求 2x=4 的解"}]

    called = {"solve": False}

    async def fake_handle_question(*a, **k):
        called["solve"] = True
        return {"answer": "x", "steps": []}

    monkeypatch.setattr(tenant_llm, "resolve_llm_config", fake_resolve)
    monkeypatch.setattr(ocr, "recognize", fake_ocr)
    monkeypatch.setattr(assignment_match, "load_assignment_questions", fake_lookup)
    monkeypatch.setattr(orchestrator, "handle_question", fake_handle_question)

    out = asyncio.run(orchestrator.handle_image(
        b"img", tenant_id="t", student_id="s1", require_assignment=True,
    ))
    assert out["not_in_assignment"] is True
    assert out["degraded"] is True
    assert out["grading_allowed"] is False
    assert called["solve"] is False, "不在作业中的题不得继续解题/判分"
    assert "不在你的作业" in out["message"]


def test_handle_image_continues_when_question_matches(monkeypatch):
    async def fake_resolve(_t):
        return _cfg()

    async def fake_ocr(*a, **k):
        return "求 2x=4 的解"

    async def fake_lookup(**_kw):
        return [{"seq": 1, "stem": "求 2x=4 的解", "answer": "x=2"}]

    captured = {}

    async def fake_handle_question(q, **k):
        captured["ref"] = k.get("reference_answer")
        return {"answer": "x=2", "steps": []}

    monkeypatch.setattr(tenant_llm, "resolve_llm_config", fake_resolve)
    monkeypatch.setattr(ocr, "recognize", fake_ocr)
    monkeypatch.setattr(assignment_match, "load_assignment_questions", fake_lookup)
    monkeypatch.setattr(orchestrator, "handle_question", fake_handle_question)

    out = asyncio.run(orchestrator.handle_image(
        b"img", tenant_id="t", student_id="s1", require_assignment=True,
    ))
    assert out["not_in_assignment"] is False
    assert out["grading_allowed"] is True
    assert captured["ref"] == "x=2"


def test_handle_image_free_mode_skips_assignment_check(monkeypatch):
    """灰度关闭时保持旧行为（不查作业、直接识别），便于回退。"""
    async def fake_resolve(_t):
        return _cfg()

    async def fake_ocr(*a, **k):
        return "任意题"

    async def fake_lookup(**_kw):
        raise AssertionError("free 模式不应查作业")

    async def fake_handle_question(*a, **k):
        return {"answer": "ok", "steps": []}

    monkeypatch.setattr(tenant_llm, "resolve_llm_config", fake_resolve)
    monkeypatch.setattr(ocr, "recognize", fake_ocr)
    monkeypatch.setattr(assignment_match, "load_assignment_questions", fake_lookup)
    monkeypatch.setattr(orchestrator, "handle_question", fake_handle_question)

    out = asyncio.run(orchestrator.handle_image(
        b"img", tenant_id="t", student_id="s1", require_assignment=False,
    ))
    assert out["not_in_assignment"] is False
    assert out["answer"] == "ok"
