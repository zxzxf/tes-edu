"""M2-11 解析/归因提示词规范 + 数值一致性护栏。

背景：题型收敛后**判分主链路已不调 LLM**，但"解析生成"与"错因归因"仍在调模型。
模型在这两处最容易犯的错，是写出与卷面答案冲突的数值——例如标答 61/25，
解析里写「答案约为 3.04」。学生照解析订正，等于被系统带着改错。

本文件锁住三件事：
1. 数值抽取/冲突判定（分数、小数、±、多解都能认，且不误判区间端点）；
2. 解析与归因的输出经护栏后就地修正，或显式标记冲突；
3. 提示词里写明约束，且 PROMPT_VERSION 已 bump（否则旧缓存会绕过新规则）。
"""
import pytest

from app.core import config, numeric_guard, prompts


# ------------------------------------------------------------------ 数值解析
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("61/25", 2.44),
        ("2.44", 2.44),
        ("4", 4.0),
        ("-0.5", -0.5),
        ("-1/2", -0.5),
        ("\\frac{61}{25}", 2.44),
        ("$3$", 3.0),
    ],
)
def test_parse_number_accepts_common_forms(raw, expected):
    assert numeric_guard.parse_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["±2", "a=1", "x>0", "", None, "(2,3)"])
def test_parse_number_rejects_non_scalar(raw):
    assert numeric_guard.parse_number(raw) is None


def test_answer_values_expands_multi_solution():
    vals = numeric_guard.answer_values("2 或 -2")
    assert vals == pytest.approx([2.0, -2.0])


def test_answer_values_handles_pm_notation():
    assert numeric_guard.answer_values("±2") == pytest.approx([2.0, -2.0])


# -------------------------------------------------------------- 冲突检测
def test_conflict_detects_wrong_claim():
    text = "所以本题答案为 3.04。"
    assert numeric_guard.conflicting_claims(text, "61/25") == [pytest.approx(3.04)]


def test_no_conflict_when_claim_matches_reference():
    assert numeric_guard.conflicting_claims("答案为 61/25。", "61/25") == []


def test_conflict_ignores_worked_example_numbers():
    """推导过程中的中间值不是"结论数值"，不该被当成冲突。"""
    text = "先求期望：1/25 + 60/25 = 61/25，所以答案为 61/25。"
    assert numeric_guard.conflicting_claims(text, "61/25") == []


def test_conflict_unknown_reference_never_flags():
    """没有可信标答时一律不判冲突——宁可不管，也不能瞎改。"""
    assert numeric_guard.conflicting_claims("答案为 3.04。", None) == []
    assert numeric_guard.conflicting_claims("答案为 3.04。", "A") == []
    assert numeric_guard.conflicting_claims("答案为 3.04。", "x=1") == []


def test_pm_reference_is_trusted_for_conflict_check():
    """±2 是合法的多解标答，3.04 与它冲突。"""
    assert numeric_guard.conflicting_claims("答案为 3.04。", "±2") == [pytest.approx(3.04)]


# ---------------------------------------------------------------- 护栏
def test_guard_rewrites_conflicting_answer_claim():
    out = numeric_guard.guard_analysis(
        {"answer": "所以答案为 3.04。", "steps": ["代入得 61/25，故答案为 3.04"]},
        "61/25",
    )
    assert "3.04" not in out["answer"]
    assert "2.44" in out["answer"]
    assert "3.04" not in out["steps"][0]


def test_guard_rewrites_every_conclusion_occurrence():
    """同一错误值出现在 answer 与 steps 时都要改，不能留下自相矛盾。"""
    out = numeric_guard.guard_analysis(
        {"answer": "答案为 3.04。", "steps": ["答案为 3.04"]}, "61/25"
    )
    assert out["numeric_conflict"] is False
    assert "3.04" not in out["answer"]
    assert "3.04" not in out["steps"][0]


def test_guard_keeps_intermediate_numbers_untouched():
    """推导中间值（60/25）不是结论，护栏既不该判冲突，也不该改它。"""
    out = numeric_guard.guard_analysis(
        {"answer": "由 60/25 + 1/25 = 61/25，答案为 61/25。", "steps": []}, "61/25"
    )
    assert out["numeric_conflict"] is False
    assert "60/25" in out["answer"]


def test_guard_noop_when_no_trusted_reference():
    src = {"answer": "答案为 3.04。", "steps": []}
    out = numeric_guard.guard_analysis(src, None)
    assert out["answer"] == src["answer"]
    assert out.get("numeric_conflict") is False


# ---------------------------------------------------------------- 提示词
def test_prompt_version_bumped_for_m2_11():
    assert int(config.PROMPT_VERSION) >= 3


def test_analysis_prompt_forbids_conflicting_numbers():
    text = prompts.ANALYSIS_SYSTEM
    assert "标准答案" in text
    assert "不得" in text
    assert "数值" in text


def test_attribution_prompt_forbids_conflicting_numbers():
    text = prompts.ATTRIBUTION_SYSTEM
    assert "错因" in text
    assert "不得" in text
    assert "数值" in text


def test_prompt_version_constant_is_single_source():
    """提示词版本必须只有一处定义，避免改了一边、缓存键读另一边。"""
    assert prompts.PROMPT_VERSION == config.PROMPT_VERSION
