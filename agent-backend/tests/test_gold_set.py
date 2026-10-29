"""M2-8 Gold set 回归门禁（MVP 先用 5 道线上验证题，后续扩到 30~50 题）。

这 5 道题是此前线上反复踩坑的样本：歧义 a=1、等比 ±2、切线 a=4、期望 61/25、
平均数选 C。任何一道的预期判定变了，都等于正确率回退，必须在 CI 卡住。

全部走 ``equiv`` 纯函数（0 token），适合高频 CI 回归；含选项内容的选择题
通过 ``options`` 映射识别「只写数值」的选法。
"""
import pytest

from app.core.equiv import blank_equivalent, choice_equivalent

# (题名, 判定种类, ref, student, options, expected)
# kind: "blank" -> blank_equivalent ; "choice" -> choice_equivalent(multi=False)
CASES = [
    # 1. 歧义 a=1（主解 answer=1，学生写 a=1 必须对，不能被判错）
    ("歧义_a=1_prefix", "blank", "1", "a=1", None, True),
    ("歧义_a=1_num", "blank", "1", "1", None, True),
    ("歧义_a=1_wrong", "blank", "1", "2", None, False),

    # 2. 等比 ±2（标答 2 或 -2，学生写任一个都对；写 3 错）
    ("等比_±2_v1", "blank", "2 或 -2", "2", None, True),
    ("等比_±2_v2", "blank", "2 或 -2", "-2", None, True),
    ("等比_±2_pm", "blank", "2 或 -2", "±2", None, True),
    ("等比_±2_wrong", "blank", "2 或 -2", "3", None, False),

    # 3. 切线 a=4（标答 4，a=4 / 4 都对；3 错）
    ("切线_a=4_prefix", "blank", "4", "a=4", None, True),
    ("切线_a=4_num", "blank", "4", "4", None, True),
    ("切线_a=4_wrong", "blank", "4", "3", None, False),

    # 4. 期望 61/25（分数 ≡ 小数 2.44，答 3 错且点评不得写 3.04）
    ("期望_61/25_eq", "blank", "61/25", "61/25", None, True),
    ("期望_61/25_dec", "blank", "61/25", "2.44", None, True),
    ("期望_61/25_ex", "blank", "61/25", "E(X)=61/25", None, True),
    ("期望_61/25_wrong", "blank", "61/25", "3", None, False),

    # 5. 平均数选 C（选项 A=8 B=9 C=12 D=18；C/c/12/C.12 都认，A/9 不认）
    ("平均_C", "choice", "C", "C", {"A": "8", "B": "9", "C": "12", "D": "18"}, True),
    ("平均_c", "choice", "C", "c", {"A": "8", "B": "9", "C": "12", "D": "18"}, True),
    ("平均_12", "choice", "C", "12", {"A": "8", "B": "9", "C": "12", "D": "18"}, True),
    ("平均_Cdot", "choice", "C", "C. 12", {"A": "8", "B": "9", "C": "12", "D": "18"}, True),
    ("平均_A", "choice", "C", "A", {"A": "8", "B": "9", "C": "12", "D": "18"}, False),
    ("平均_9", "choice", "C", "9", {"A": "8", "B": "9", "C": "12", "D": "18"}, False),
]


@pytest.mark.parametrize("name,kind,ref,stu,opts,exp", CASES)
def test_gold_set(name, kind, ref, stu, opts, exp):
    if kind == "choice":
        got = choice_equivalent(stu, ref, multi=False, options=opts)
    else:
        got = blank_equivalent(stu, ref)
    assert got == exp, f"{name}: expected {exp!r}, got {got!r}"
