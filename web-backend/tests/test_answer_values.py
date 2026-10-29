"""M2-4 答案值预展开（``answer_values``）回归。

背景：M2-3 之后「标答形态判不了」的题一律转人工。但同一道题反复转人工没有意义：
标答是 ``sqrt(66)/6`` 时，老师第一次确认「1.354 也算对」之后，就该自动判对。

本模块固化两件事：
1. ``expand_answer_values`` 只做**等价展开**（不新增标答未蕴含的值）。
2. ``blank_judge(values=...)`` 命中即判对、**只救对不判错**；多空题不得用扁平集合
   顶替逐空比对（否则「空1 填了空2 的答案」也会判对，属静默误判）。
"""
from app.core.equiv import blank_judge, expand_answer_values


# --------------------------------------------------------------------------
# 展开
# --------------------------------------------------------------------------
def test_expand_pm_derives_both_values():
    assert expand_answer_values("±2") == ["2", "-2"]


def test_expand_multi_solution_keeps_raw_and_stripped():
    """``x=-1 或 x=2`` 既保留原写法（学生照抄），也给出变量右侧值（学生只写 -1）。"""
    vals = expand_answer_values("x=-1 或 x=2")
    assert "x=-1" in vals and "-1" in vals
    assert "x=2" in vals and "2" in vals


def test_expand_multi_blank_splits_per_blank():
    assert expand_answer_values("2;3") == ["2", "3"]


def test_expand_interval_stays_whole():
    """区间内的逗号不是分隔符，展开后仍是一个整体。"""
    vals = expand_answer_values("[2,3)")
    assert len(vals) == 1 and "2" in vals[0] and "3" in vals[0]


def test_expand_symbolic_answer_is_itself_only():
    """符号答案不做额外猜测展开（漏解补全是教师的判断，不是程序的）。"""
    assert expand_answer_values("sqrt(66)/6") == ["sqrt(66)/6"]


def test_expand_equation_does_not_yield_bare_zero():
    """方程 3x+4y-5=0 不能展开出 '0'，否则学生写 0 就白拿分。"""
    vals = expand_answer_values("3x+4y-5=0")
    assert "0" not in vals


def test_expand_preserves_order_and_dedupes():
    vals = expand_answer_values("2 或 2")
    assert vals.count("2") == 1


def test_expand_empty_answer():
    assert expand_answer_values("") == []
    assert expand_answer_values(None) == []


# --------------------------------------------------------------------------
# 判分：命中即判对
# --------------------------------------------------------------------------
def test_values_rescue_symbolic_answer():
    """核心场景：标答符号形态本来转人工，教师确认 1.354 后自动判对。"""
    assert blank_judge("1.354", "sqrt(66)/6")["status"] == "auto"
    assert blank_judge("1.354", "sqrt(66)/6")["correct"] is False   # 差在容差外

    v = blank_judge("1.354", "sqrt(66)/6", values=["sqrt(66)/6", "1.354"])
    assert v["status"] == "auto" and v["correct"] is True
    assert v["reason"] == "命中已确认答案值"


def test_values_accept_teacher_confirmed_missing_solution():
    """标答只写了 2（漏解），教师把 -2 确认为可接受值后，学生答 -2 判对。"""
    assert blank_judge("-2", "2")["correct"] is False
    assert blank_judge("-2", "2", values=["2", "-2"])["correct"] is True


def test_values_never_turn_correct_into_wrong():
    """红线：values 只救对，不能因为 values 不含学生答案就把本来就是对的判错。"""
    assert blank_judge("2", "2", values=["999"])["correct"] is True


def test_values_do_not_mask_wrong_answer():
    assert blank_judge("5", "2", values=["2", "-2"])["correct"] is False


def test_values_ignored_for_multi_blank():
    """多空题：扁平值集合不能顶替逐空比对，否则空位错位也会判对。"""
    # 空1 写了本该属于空2 的答案（3），空2 写了空1 的答案（2）
    v = blank_judge("3;2", "2;3", values=["2", "3"])
    assert v["status"] == "auto" and v["correct"] is False

    # 正常逐空全对仍然判对
    v2 = blank_judge("2;3", "2;3", values=["2", "3"])
    assert v2["correct"] is True


def test_values_empty_list_behaves_as_before():
    assert blank_judge("2", "2", values=[])["correct"] is True
    assert blank_judge("3", "2", values=[])["correct"] is False


def test_values_blank_student_answer_not_rescued():
    """学生空着不答，不能被 values 里某个空串/值救成对。"""
    v = blank_judge("", "2", values=["2", ""])
    assert v["correct"] is False


def test_values_auto_gradeable_symbolic_now_gradable():
    """完整链路：方程标答本来转人工，教师确认等价写法后判分可闭环。

    ``3x+4y-5=0`` 与 ``y=(5-3x)/4`` 数学等价但字符串不同、都无法数值化 ⇒ manual；
    教师在工单里勾「这种写法也算对」后落进 answer_values ⇒ 同题后续自动判对。
    """
    ref = "3x+4y-5=0"
    assert blank_judge("y=(5-3x)/4", ref)["status"] == "manual"
    v = blank_judge("y=(5-3x)/4", ref, values=[ref, "y=(5-3x)/4"])
    assert v["status"] == "auto" and v["correct"] is True
