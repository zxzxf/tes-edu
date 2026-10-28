"""点评「等价数值」幻觉护栏（2026-10-29 线上回归）。

现象：题「有放回取 3 次，至少被取出一次的球数 X 的期望」标准答案 61/25。
学生答 3 判错时，v1 点评写成「正确答案为61/25（或3.04）」——61/25=2.44，
3.04 是模型凭空编的等价写法。学生照这条点评订正，等于被系统带着改错。

红线：**系统已有可信标准答案时，点评里出现的"等价数值"必须与它数值相等。**
"""
import asyncio

import pytest

from app.core import llm, orchestrator


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- _ref_numeric

def test_ref_numeric_parses_fraction_and_decimal():
    """分数、小数、整数都能解析；带正负号/无法解析的返回 None。"""
    assert orchestrator._ref_numeric("61/25") == pytest.approx(2.44)
    assert orchestrator._ref_numeric("2.44") == pytest.approx(2.44)
    assert orchestrator._ref_numeric("4") == pytest.approx(4.0)
    assert orchestrator._ref_numeric("-1/2") == pytest.approx(-0.5)
    assert orchestrator._ref_numeric("±2") is None
    assert orchestrator._ref_numeric("a=1") is None
    assert orchestrator._ref_numeric("") is None
    assert orchestrator._ref_numeric(None) is None


# ------------------------------------------------- _sanitize_comment_numbers

def test_sanitize_replaces_wrong_equivalent():
    """（或3.04）与 61/25 不等价 -> 就地改成 2.44。"""
    out = orchestrator._sanitize_comment_numbers(
        "正确答案为61/25（或3.04），学生作答3错误。", "61/25"
    )
    assert "3.04" not in out
    assert "2.44" in out
    assert "61/25" in out


def test_sanitize_keeps_correct_equivalent():
    """（或2.44）本来就对，一个字都不许动。"""
    s = "正确答案为61/25（或2.44）。"
    assert orchestrator._sanitize_comment_numbers(s, "61/25") == s


def test_sanitize_noop_without_ref_or_non_numeric_ref():
    """没有可信标准答案（无 ref / 非纯数值）时原样返回——宁可不改，也不瞎改。"""
    s = "正确答案为61/25（或3.04）。"
    assert orchestrator._sanitize_comment_numbers(s, None) == s
    assert orchestrator._sanitize_comment_numbers(s, "±2") == s
    assert orchestrator._sanitize_comment_numbers("", "61/25") == ""


def test_sanitize_does_not_touch_standard_answer_hint():
    """judge_v2 自带的「（本题标准答案：4）」不含"或/即/等于"，不受影响。"""
    s = "再检查一下代入过程。（本题标准答案：4）"
    assert orchestrator._sanitize_comment_numbers(s, "4") == s


def test_sanitize_handles_halfwidth_brackets_and_connectives():
    """半角括号与"即/等于"同样识别。"""
    assert "2.44" in orchestrator._sanitize_comment_numbers("答案 61/25(即3.04)", "61/25")
    assert "2.44" in orchestrator._sanitize_comment_numbers("答案 61/25（等于3.04）", "61/25")


# ---------------------------------------------------------------- 链路集成

def test_fallback_comment_is_sanitized(monkeypatch):
    """v1 兜底点评回包前必须过数值护栏（这是线上真实出问题的那条路径）。"""

    async def _fake_v2(question, student_answer, context=None, llm_cfg=None, ref_answer=None):
        return {
            "correct": None,
            "partial": False,
            "comment": "未匹配到标注题，转人工复核",
            "first_wrong_step": None,
            "error_type": "unknown",
            "evidence": "",
            "correction": "",
            "attribution_source": "unknown",
            "need_process": False,
        }

    async def _fake_v1(question, student_answer, context=None, llm_cfg=None,
                       ref_answer=None, tenant_id=None, **kwargs):
        assert ref_answer == "61/25"
        return {"correct": False, "partial": False,
                "comment": "正确答案为61/25（或3.04），学生作答3错误。"}

    monkeypatch.setattr(orchestrator.judge_v2, "judge", _fake_v2)
    monkeypatch.setattr(orchestrator.llm, "judge", _fake_v1)

    j = _run(orchestrator._judge_v2_with_fallback(
        "箱中有5个球…E(X)=_____", "3", {}, ref_answer="61/25", tenant_id="school_pilot_01",
    ))
    assert j["correct"] is False
    assert "3.04" not in j["comment"]
    assert "2.44" in j["comment"]
