"""M2-3 填空确定性判定回归门禁。

样本取自**题库真实数据**（`course_kb` 高三数学 21 道填空题的 answer 原文），
不是编造的干净用例——这批答案里有多解包裹、区间并集、LaTeX 根式、带变量名、
含变量表达式，正是线上会遇到的形态。

三条持续红线（改动 equiv 时必须重新跑本文件）：

1. **自判必须全对**：学生照抄系统答案不能判错，也不能转人工。
2. **能数值化的答错必须判错**：不能让「都判对」式的宽松掩盖判分能力。
3. **解析不了的绝不判错**：含变量/符号函数/方程，答错一律转人工。
"""
import pytest

from app.core.equiv import (
    blank_auto_gradeable,
    blank_equivalent,
    blank_judge,
    normalize_interval,
    norm_text,
    split_blanks,
    split_multi,
    values_equivalent,
)

# ---------------------------------------------------------------- 题库真实标答
BANK_ANSWERS = [
    "1",
    "x = -3/2",
    "1",
    "5；720 - 240(n+3)/2^n",
    "-28",
    "3x+4y-5=0（或 x=-1 或 7x-24y-25=0）",
    "(-∞,-4)∪(0,+∞)",
    "13",
    "64",
    "7√6/6",
    "[2,3)",
    "3√5/5",
    "3/2",
    "ln2",
    "1/2",
    "4",
    "2",
    r"$\dfrac{61}{25}$",
    r"$\dfrac{\sqrt{66}}{6}$（或 $\sqrt{\dfrac{11}{6}}$）",
    r"$\theta=\dfrac{3\pi}{2}$，$f\!\left(\dfrac{2\pi}{3}\right)=1$",
    r"$\sqrt[3]{\dfrac32}$",
]

#: 这 3 道标答含变量/方程/函数等式，只能字符串判对、答错转人工
NOT_AUTO_GRADEABLE = [
    "5；720 - 240(n+3)/2^n",
    "3x+4y-5=0（或 x=-1 或 7x-24y-25=0）",
    r"$\theta=\dfrac{3\pi}{2}$，$f\!\left(\dfrac{2\pi}{3}\right)=1$",
]


@pytest.mark.parametrize("ref", BANK_ANSWERS)
def test_bank_answer_selfjudge_is_correct(ref):
    """红线 1：学生照抄标答必须判对（含多解包裹/嵌套 LaTeX/区间并集）。"""
    v = blank_judge(ref, ref)
    assert v["status"] == "auto", f"自判不应转人工：{ref} -> {v}"
    assert v["correct"] is True, f"自判不应判错：{ref} -> {v}"
    assert blank_equivalent(ref, ref) is True


def test_blank_auto_gradeable_matches_bank_distribution():
    """18/21 可自动判，3 道含变量/方程/函数等式不可（用于发布时算人工量）。"""
    auto = [a for a in BANK_ANSWERS if blank_auto_gradeable(a)]
    manual = [a for a in BANK_ANSWERS if not blank_auto_gradeable(a)]
    assert len(auto) == 18
    assert manual == NOT_AUTO_GRADEABLE


# ---------------------------------------------------------------- 等价写法
def test_fraction_decimal_equiv():
    assert blank_judge("61/25", r"$\dfrac{61}{25}$")["correct"] is True
    assert blank_judge("2.44", r"$\dfrac{61}{25}$")["correct"] is True
    assert blank_judge("2.5", "61/25")["correct"] is False      # 2.44 ≠ 2.5
    assert blank_judge("3", "61/25")["correct"] is False


def test_handwritten_rounding_is_accepted():
    """学生手算根式只写 6~7 位小数，不能被判错（判错不可逆）。"""
    assert blank_judge("2.857738", "7√6/6")["correct"] is True          # 2.85773803...
    assert blank_judge("1.3416407", "3√5/5")["correct"] is True         # 1.34164078...
    assert blank_judge("1.1447142", r"$\sqrt[3]{\dfrac32}$")["correct"] is True
    # 但差得远必须是错
    assert blank_judge("2.85", "7√6/6")["correct"] is False


def test_variable_prefixed_answer():
    assert blank_judge("-1.5", "x = -3/2")["correct"] is True
    assert blank_judge("x=-3/2", "x = -3/2")["correct"] is True


def test_interval_union_commutative():
    ref = "(-∞,-4)∪(0,+∞)"
    assert blank_judge("(0,+∞)U(-∞,-4)", ref)["correct"] is True
    assert blank_judge("(-∞,-4)∪(0,+∞)", ref)["correct"] is True
    assert normalize_interval("(-∞,-4)∪(0,+∞)") == normalize_interval("(0,+∞)∪(-∞,-4)")
    # 半开区间端点不同 -> 明确判错
    assert blank_judge("[2,3]", "[2,3)")["correct"] is False
    assert blank_judge("(0,1)", "[2,3)")["correct"] is False


def test_multi_solution_either_branch_counts():
    """多解：学生写出其中任一解都判对（含无分隔符包裹写法）。"""
    ref = "3x+4y-5=0（或 x=-1 或 7x-24y-25=0）"
    assert blank_judge("x=-1", ref)["correct"] is True
    assert blank_judge("3x+4y-5=0", ref)["correct"] is True
    assert split_multi(ref) == ["3x+4y-5=0", "x=-1", "7x-24y-25=0"]
    # 红线：方程右侧的 0 不是解，学生写 0 不能判对
    assert blank_judge("0", ref)["status"] == "manual"


def test_nested_latex_multi_solution_splits():
    """题 19：包裹括号内还有嵌套 sqrt(...)，配对扫描不能被内层括号骗到。"""
    ref = r"$\dfrac{\sqrt{66}}{6}$（或 $\sqrt{\dfrac{11}{6}}$）"
    parts = split_multi(ref)
    assert len(parts) == 2
    assert parts[0] == "(sqrt(66))/(6)"
    assert parts[1] == "sqrt((11)/(6))"
    # 两个解数值其实相等（66/36 == 11/6），学生写小数时任一分支都应命中
    assert blank_judge("1.3540064", ref)["correct"] is True


# ---------------------------------------------------------------- 多空
def test_multi_blank_structure_not_split_by_inner_comma():
    """结构解析：区间/函数内部逗号不能被当空分隔符。"""
    assert split_blanks("[2,3)") == ["[2,3)"]
    assert split_blanks("(-∞,-4)∪(0,+∞)") == ["(-inf,-4)|(0,inf)"]
    # 幂符号已归一（^ -> **），这里只校验切分位置：只在顶层「；」处断开
    assert split_blanks("5；720 - 240(n+3)/2^n") == ["5", "720 - 240(n+3)/2**n"]
    # 题 20：两个空分别是一个等式，内部括号里的逗号不能切
    assert len(split_blanks(r"$\theta=\dfrac{3\pi}{2}$，$f\!\left(\dfrac{2\pi}{3}\right)=1$")) == 2


def test_multi_blank_all_or_nothing():
    ref = "5；720 - 240(n+3)/2^n"
    assert blank_judge("", ref, blanks=["5", "720 - 240(n+3)/2^n"])["correct"] is True
    # 第一空对、第二空是符号表达式无法证明不等 -> 转人工（不判错）
    v = blank_judge("", ref, blanks=["5", "abc"])
    assert v["status"] == "manual"
    # 两空都空 -> 明确判错
    assert blank_judge("", ref, blanks=["", ""])["correct"] is False


def test_multi_blank_numeric_second_blank_can_be_wrong():
    ref = "1；2"
    assert blank_judge("", ref, blanks=["1", "2"])["correct"] is True
    assert blank_judge("", ref, blanks=["1", "3"])["correct"] is False
    assert blank_judge("", ref, blanks=["1", ""])["correct"] is False


def test_blank_count_mismatch_goes_manual():
    """空数不符且学生有作答 -> 转人工（可能用了别的分隔符），不硬判错。"""
    v = blank_judge("1", "1；2")
    assert v["status"] == "manual"
    assert "空数不符" in v["reason"]


# ---------------------------------------------------------------- 转人工红线
def test_symbolic_mismatch_never_wrong():
    """解析不了的符号答错，一律转人工——绝不硬判错。"""
    for stu, ref in [
        ("ln3", "720 - 240(n+3)/2^n"),
        ("abc", "720 - 240(n+3)/2^n"),
        ("3x+4y-5=1", "3x+4y-5=0（或 x=-1 或 7x-24y-25=0）"),
        ("0", "3x+4y-5=0（或 x=-1 或 7x-24y-25=0）"),
    ]:
        v = blank_judge(stu, ref)
        assert v["status"] == "manual", f"{stu} vs {ref} 应转人工，实际 {v}"
        assert v["correct"] is None
        assert v["reason"]


def test_empty_answer_is_wrong():
    assert blank_judge("", "1")["correct"] is False
    assert blank_judge("   ", "1")["correct"] is False


def test_blank_equivalent_keeps_bool_semantics():
    """agent judge_v2 用的兼容语义：manual 一律 False（交 LLM 兜底），不误判对。"""
    assert blank_equivalent("1", "1") is True
    assert blank_equivalent("2", "1") is False
    assert blank_equivalent("ln3", "720 - 240(n+3)/2^n") is False


# ---------------------------------------------------------------- 归一化回归
def test_norm_text_function_shorthand():
    """ln2 -> ln(2)：题库有 ln2 这种写法，归一后才能数值化。"""
    assert norm_text("ln2") == "ln(2)"
    assert values_equivalent("0.6931471805599453", "ln2")
    assert not values_equivalent("ln3", "ln2")


def test_norm_text_keeps_interval_structure():
    """归一化不能剥掉区间端点括号。

    早期实现用 ``strip('()')`` 清理多解包裹残留，会把半开区间 ``[2,3)``
    剥成 ``[2,3``、把 ``(sqrt(66))/(6)`` 剥成 ``sqrt(66))/(6``，直接造成误判。
    """
    assert "[2,3)" in norm_text("[2,3)")
    assert norm_text("[2,3)").startswith("[")
    assert norm_text("[2,3)").endswith(")")
