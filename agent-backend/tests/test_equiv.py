"""M2-1/2 等价判定纯函数单测：选择题字母/集合等价、填空逐空+多解展开、
分数≡小数数值等价。全部 0 token，无 LLM / DB 依赖。
"""
import pytest

from app.core.equiv import (
    blank_equivalent,
    choice_equivalent,
    norm_choice_label,
    norm_text,
    split_multi,
    strip_lead,
    to_number,
    values_equivalent,
)


# ---------------------------------------------------------------- 归一化
def test_norm_text_unifies_fullwidth_and_punct():
    assert norm_text("Ａ＝１／２") == "a=1/2"
    assert norm_text("  E(X)=61/25  ") == "e(x)=61/25"
    assert norm_text("（2，3）") == "(2,3)"


def test_strip_lead_removes_chinese_prefix():
    assert strip_lead("答案为4") == "4"
    assert strip_lead("选 A") == "A"
    assert strip_lead("答案为选 A") == "A"  # 叠写两轮
    assert strip_lead("4") == "4"


# ---------------------------------------------------------------- 数值等价
def test_to_number_parses_fraction_and_decimal():
    assert to_number("61/25") == pytest.approx(2.44)
    assert to_number("2.44") == pytest.approx(2.44)
    assert to_number("-1/2") == pytest.approx(-0.5)
    assert to_number("4") == pytest.approx(4.0)
    assert to_number("±2") is None
    assert to_number("a=1") is None
    assert to_number("") is None
    assert to_number(None) is None


def test_values_equivalent_fraction_decimal():
    # 分数 ≡ 小数（判分层此前缺失的关键能力）
    assert values_equivalent("61/25", "2.44")
    assert values_equivalent("2.44", "61/25")
    # 归一字符串相等
    assert values_equivalent("2", "2.0")
    assert values_equivalent("±2", "±2")
    # 不等价
    assert not values_equivalent("61/25", "3")      # 3.04 幻觉之类必须判不等
    assert not values_equivalent("1", "12")          # 不能标答自匹配
    # 带变量名/引导词不属于本层职责（交给 blank_equivalent 处理）
    assert not values_equivalent("a=1", "1")
    assert not values_equivalent("答案为4", "4")


# ---------------------------------------------------------------- 多解展开
def test_split_multi_expands_or_and_pm():
    assert split_multi("2 或 -2") == ["2", "-2"]
    assert split_multi("±2") == ["2", "-2"]
    assert split_multi("1;2") == ["1", "2"]
    assert split_multi("4") == ["4"]


# ---------------------------------------------------------------- 选择题
def test_norm_choice_label():
    assert norm_choice_label("C") == "C"
    assert norm_choice_label("c") == "C"
    assert norm_choice_label("C. 12") == "C"
    assert norm_choice_label("C.12") == "C"
    assert norm_choice_label("12") is None          # 纯数值不识别为标签
    assert norm_choice_label("") is None


def test_choice_single_letter_equiv():
    assert choice_equivalent("C", "C") is True
    assert choice_equivalent("c", "C") is True
    assert choice_equivalent("C. 12", "C") is True
    assert choice_equivalent("A", "C") is False
    assert choice_equivalent("9", "C") is False


def test_choice_single_with_numeric_option_content():
    opts = {"A": "8", "B": "9", "C": "12", "D": "18"}
    # 学生只写数值 12 == 选 C（选项内容恰为 12）
    assert choice_equivalent("12", "C", options=opts) is True
    # 写 9 是 B 的内容，不是 C
    assert choice_equivalent("9", "C", options=opts) is False


def test_choice_multi_set_equiv_and_partial():
    # 多选集合相等
    assert choice_equivalent("A,B", "B,A") is True
    assert choice_equivalent("B,A", "A,B") is True
    # 漏选但所选皆正确 -> partial
    assert choice_equivalent("A", "A,B", multi=True) == "partial"
    # 多选错选 -> False（非 partial）
    assert choice_equivalent("A,C", "A,B", multi=True) is False


# ---------------------------------------------------------------- 填空
def test_blank_value_and_lead_and_multi():
    assert blank_equivalent("a=1", "1") is True          # 带变量名
    assert blank_equivalent("答案为4", "4") is True      # 带引导词
    assert blank_equivalent("4", "4") is True
    assert blank_equivalent("a=4", "4") is True
    assert blank_equivalent("3", "4") is False
    # 多解：标答给「2 或 -2」，学生写其一即对
    assert blank_equivalent("2", "2 或 -2") is True
    assert blank_equivalent("-2", "2 或 -2") is True
    assert blank_equivalent("±2", "2 或 -2") is True
    assert blank_equivalent("3", "2 或 -2") is False


def test_blank_fraction_decimal_equiv():
    # 分数 ≡ 小数（判分层此前缺失）
    assert blank_equivalent("2.44", "61/25") is True
    assert blank_equivalent("61/25", "2.44") is True
    assert blank_equivalent("3", "61/25") is False


def test_blank_multi_blank_all_or_nothing():
    # 多空逐空全对才 True；部分对 / 空数不符 -> False
    assert blank_equivalent("1;2", "1;2") is True
    assert blank_equivalent("1;3", "1;2") is False
    assert blank_equivalent("1", "1;2") is False
